"""Install manifest generation for the VeriLoad Kubernetes Operator."""

from __future__ import annotations

from typing import Any

import yaml

from veriload.k8s_operator.crd import build_veriloadrun_crd
from veriload.k8s_operator.resources import GROUP, PLURAL


def generate_operator_install_manifest(*, name: str, namespace: str, image: str) -> str:
    """Generate CRD, RBAC, and operator Deployment YAML."""

    docs = [
        build_veriloadrun_crd(),
        _service_account(name, namespace),
        _cluster_role(name),
        _cluster_role_binding(name, namespace),
        _deployment(name, namespace, image),
    ]
    return "---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs)


def _service_account(name: str, namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": name, "namespace": namespace},
    }


def _cluster_role(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {"name": name},
        "rules": [
            {
                "apiGroups": [GROUP],
                "resources": [PLURAL],
                "verbs": ["get", "list", "watch", "patch", "update"],
            },
            {
                "apiGroups": [GROUP],
                "resources": [f"{PLURAL}/status"],
                "verbs": ["get", "patch", "update"],
            },
            {
                "apiGroups": [GROUP],
                "resources": [f"{PLURAL}/finalizers"],
                "verbs": ["update"],
            },
            {
                "apiGroups": ["batch"],
                "resources": ["jobs"],
                "verbs": ["create", "delete", "get", "list", "patch", "update", "watch"],
            },
            {
                "apiGroups": [""],
                "resources": ["pods"],
                "verbs": ["get", "list", "watch"],
            },
            {
                "apiGroups": [""],
                "resources": ["pods/log"],
                "verbs": ["get", "list"],
            },
            {
                "apiGroups": [""],
                "resources": ["services", "configmaps", "secrets", "persistentvolumeclaims"],
                "verbs": ["create", "delete", "get", "list", "patch", "update", "watch"],
            },
            {
                "apiGroups": [""],
                "resources": ["events"],
                "verbs": ["create", "patch"],
            },
        ],
    }


def _cluster_role_binding(name: str, namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": name},
        "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": namespace}],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": name,
        },
    }


def _deployment(name: str, namespace: str, image: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/name": name}},
                "spec": {
                    "serviceAccountName": name,
                    "containers": [
                        {
                            "name": "operator",
                            "image": image,
                            "args": ["operator", "run"],
                            "env": [{"name": "PYTHONUNBUFFERED", "value": "1"}],
                        }
                    ],
                },
            },
        },
    }
