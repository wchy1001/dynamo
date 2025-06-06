#  SPDX-FileCopyrightText: Copyright (c) 2020 Atalaya Tech. Inc
#  SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#  SPDX-License-Identifier: Apache-2.0
#  #
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  #
#  http://www.apache.org/licenses/LICENSE-2.0
#  #
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#  Modifications Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import typing as t
from typing import Any

import click
import uvloop

from dynamo.runtime import DistributedRuntime, dynamo_endpoint, dynamo_worker
from dynamo.sdk import dynamo_context
from dynamo.sdk.lib.service import LinkedServices

logger = logging.getLogger(__name__)


@click.command()
@click.argument("bento_identifier", type=click.STRING, required=False, default=".")
@click.option("--service-name", type=click.STRING, required=False, default="")
@click.option(
    "--runner-map",
    type=click.STRING,
    envvar="BENTOML_RUNNER_MAP",
    help="JSON string of runners map, default sets to envars `BENTOML_RUNNER_MAP`",
)
@click.option(
    "--worker-env", type=click.STRING, default=None, help="Environment variables"
)
@click.option(
    "--worker-id",
    required=False,
    type=click.INT,
    default=None,
    help="If set, start the server as a bare worker with the given worker ID. Otherwise start a standalone server with a supervisor process.",
)
def main(
    bento_identifier: str,
    service_name: str,
    runner_map: str | None,
    worker_env: str | None,
    worker_id: int | None,
) -> None:
    """Start a worker for the given service - either Dynamo or regular service"""
    from _bentoml_impl.loader import import_service
    from bentoml._internal.container import BentoMLContainer
    from bentoml._internal.context import server_context

    from dynamo.sdk.lib.logging import configure_server_logging

    dynamo_context["service_name"] = service_name
    dynamo_context["runner_map"] = runner_map
    dynamo_context["worker_id"] = worker_id

    # Ensure environment variables are set before we initialize
    if worker_env:
        env_list: list[dict[str, t.Any]] = json.loads(worker_env)
        if worker_id is not None:
            worker_key = worker_id - 1
            if worker_key >= len(env_list):
                raise IndexError(
                    f"Worker ID {worker_id} is out of range, "
                    f"the maximum worker ID is {len(env_list)}"
                )
            os.environ.update(env_list[worker_key])

    service = import_service(bento_identifier)
    if service_name and service_name != service.name:
        service = service.find_dependent_by_name(service_name)

    configure_server_logging(service_name=service_name, worker_id=worker_id)
    if runner_map:
        BentoMLContainer.remote_runner_mapping.set(
            t.cast(t.Dict[str, str], json.loads(runner_map))
        )

    # TODO: test this with a deep chain of services
    LinkedServices.remove_unused_edges()
    # Check if Dynamo is enabled for this service
    if service.is_dynamo_component():
        if worker_id is not None:
            server_context.worker_index = worker_id

        @dynamo_worker()
        async def worker(runtime: DistributedRuntime):
            global dynamo_context
            dynamo_context["runtime"] = runtime
            if service_name and service_name != service.name:
                server_context.service_type = "service"
            else:
                server_context.service_type = "entry_service"

            server_context.service_name = service.name

            # Get Dynamo configuration and create component
            namespace, component_name = service.dynamo_address()
            logger.info(f"Registering component {namespace}/{component_name}")
            component = runtime.namespace(namespace).component(component_name)

            try:
                # if a custom lease is specified we need to create the service with that lease
                lease = None
                if service._dynamo_config.custom_lease:
                    lease = await component.create_service_with_custom_lease(
                        ttl=service._dynamo_config.custom_lease.ttl
                    )
                    lease_id = lease.id()
                    dynamo_context["lease"] = lease
                    logger.info(
                        f"Created {service.name} component with custom lease id {lease_id}"
                    )
                else:
                    # Create service first
                    await component.create_service()
                    logger.info(f"Created {service.name} component")

                # Set runtime on all dependencies
                for dep in service.dependencies.values():
                    dep.set_runtime(runtime)
                    logger.debug(f"Set runtime for dependency: {dep}")

                # Then register all Dynamo endpoints
                dynamo_endpoints = service.get_dynamo_endpoints()
                if not dynamo_endpoints:
                    error_msg = f"FATAL ERROR: No Dynamo endpoints found in service {service.name}!"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                endpoints = []
                for name, endpoint in dynamo_endpoints.items():
                    td_endpoint = component.endpoint(name)
                    logger.debug(f"Registering endpoint '{name}'")
                    endpoints.append(td_endpoint)
                    # Bind an instance of inner to the endpoint
                dynamo_context["component"] = component
                dynamo_context["endpoints"] = endpoints
                class_instance = service.inner()
                twm = []
                for name, endpoint in dynamo_endpoints.items():
                    bound_method = endpoint.func.__get__(class_instance)
                    # Only pass request type for now, use Any for response
                    # TODO: Handle a dynamo_endpoint not having types
                    # TODO: Handle multiple endpoints in a single component
                    dynamo_wrapped_method = dynamo_endpoint(endpoint.request_type, Any)(
                        bound_method
                    )
                    twm.append(dynamo_wrapped_method)
                # Run startup hooks before setting up endpoints
                for name, member in vars(class_instance.__class__).items():
                    if callable(member) and getattr(
                        member, "__bentoml_startup_hook__", False
                    ):
                        logger.debug(f"Running startup hook: {name}")
                        result = getattr(class_instance, name)()
                        if inspect.isawaitable(result):
                            # await on startup hook async_on_start
                            await result
                            logger.debug(f"Completed async startup hook: {name}")
                        else:
                            logger.info(f"Completed startup hook: {name}")
                logger.info(
                    f"Starting {service.name} instance with all registered endpoints"
                )
                # TODO:bis: convert to list
                if lease is None:
                    logger.info(f"Serving {service.name} with primary lease")
                else:
                    logger.info(f"Serving {service.name} with lease: {lease.id()}")
                result = await endpoints[0].serve_endpoint(twm[0], lease)

            except Exception as e:
                logger.error(f"Error in Dynamo component setup: {str(e)}")
                raise

        uvloop.install()
        asyncio.run(worker())


if __name__ == "__main__":
    main()
