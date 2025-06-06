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

import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from kubernetes import client, config


class K8sResource:
    def __init__(self, group: str, version: str, plural: str):
        self.group = group
        self.version = version
        self.plural = plural


DynamoDeployment = K8sResource(
    group="nvidia.com",
    version="v1alpha1",
    plural="dynamodeployments",
)


def create_custom_resource(
    group: str, version: str, namespace: str, plural: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create a custom resource in Kubernetes.

    Args:
        group: API group
        version: API version
        namespace: Target namespace
        plural: Resource plural name
        body: Resource definition

    Returns:
        Created resource
    """
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    return api.create_namespaced_custom_object(
        group=group, version=version, namespace=namespace, plural=plural, body=body
    )


def create_dynamo_deployment(
    name: str,
    namespace: str,
    dynamo_nim: str,
    labels: Dict[str, str],
    envs: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Create a DynamoDeployment custom resource.

    Args:
        name: Deployment name
        namespace: Target namespace
        dynamo_nim: Bento name and version (format: name:version)
        labels: Resource labels
        envs: Optional list of environment variables

    Returns:
        Created deployment
    """
    body = {
        "apiVersion": "nvidia.com/v1alpha1",
        "kind": "DynamoDeployment",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {"dynamoNim": dynamo_nim, "services": {}, "envs": envs if envs else []},
    }

    return create_custom_resource(
        group=DynamoDeployment.group,
        version=DynamoDeployment.version,
        namespace=namespace,
        plural=DynamoDeployment.plural,
        body=body,
    )


def get_dynamo_deployment(name: str, namespace: str) -> Dict[str, Any]:
    """
    Get a DynamoDeployment custom resource.

    Args:
        name: Deployment name
        namespace: Target namespace

    Returns:
        Deployment

    Raises:
        HTTPException: If the deployment is not found or an error occurs
    """
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    try:
        return api.get_namespaced_custom_object(
            group=DynamoDeployment.group,
            version=DynamoDeployment.version,
            namespace=namespace,
            plural=DynamoDeployment.plural,
            name=name,
        )
    except client.rest.ApiException as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail="Deployment not found")
        else:
            raise HTTPException(status_code=500, detail=str(e))


def get_namespace() -> str:
    """
    Get the namespace from the environment variable.
    """
    return os.getenv("DEFAULT_KUBE_NAMESPACE", "dynamo")


def delete_dynamo_deployment(name: str, namespace: str) -> Dict[str, Any]:
    """
    Delete a DynamoDeployment custom resource.
    """
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    try:
        return api.delete_namespaced_custom_object(
            group=DynamoDeployment.group,
            version=DynamoDeployment.version,
            namespace=namespace,
            plural=DynamoDeployment.plural,
            name=name,
        )
    except client.rest.ApiException as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail="Deployment not found")
        else:
            raise HTTPException(status_code=500, detail=str(e))


def list_dynamo_deployments(
    namespace: str,
    label_selector: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    List DynamoDeployment custom resources.

    Args:
        namespace: Target namespace
        label_selector: Optional label selector for filtering

    Returns:
        List of deployments

    Raises:
        HTTPException: If an error occurs during listing
    """
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    try:
        response = api.list_namespaced_custom_object(
            group=DynamoDeployment.group,
            version=DynamoDeployment.version,
            namespace=namespace,
            plural=DynamoDeployment.plural,
            label_selector=label_selector,
        )
        return response["items"]
    except client.rest.ApiException as e:
        raise HTTPException(status_code=500, detail=str(e))
