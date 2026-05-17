"""VeriLoadRun CustomResourceDefinition builder."""

from __future__ import annotations

from typing import Any

from veriload.k8s_operator.resources import GROUP, KIND, PLURAL, VERSION


def build_veriloadrun_crd() -> dict[str, Any]:
    """Build the VeriLoadRun CRD."""

    return {
        "apiVersion": "apiextensions.k8s.io/v1",
        "kind": "CustomResourceDefinition",
        "metadata": {"name": f"{PLURAL}.{GROUP}"},
        "spec": {
            "group": GROUP,
            "scope": "Namespaced",
            "names": {
                "plural": PLURAL,
                "singular": "veriloadrun",
                "kind": KIND,
                "shortNames": ["vlr"],
            },
            "versions": [
                {
                    "name": VERSION,
                    "served": True,
                    "storage": True,
                    "subresources": {"status": {}},
                    "additionalPrinterColumns": [
                        {"name": "Phase", "type": "string", "jsonPath": ".status.phase"},
                        {"name": "Workers", "type": "integer", "jsonPath": ".status.readyWorkers"},
                        {"name": "Failures", "type": "integer", "jsonPath": ".status.summary.totalFailures"},
                        {"name": "Age", "type": "date", "jsonPath": ".metadata.creationTimestamp"},
                    ],
                    "schema": {"openAPIV3Schema": _schema()},
                }
            ],
        },
    }


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "required": ["image", "config", "execution"],
                "properties": {
                    "image": {"type": "string", "minLength": 1},
                    "config": {
                        "type": "object",
                        "oneOf": [{"required": ["inline"]}, {"required": ["configMapRef"]}],
                        "properties": {
                            "inline": {"type": "string"},
                            "configMapRef": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "key": {"type": "string", "default": "veriload.yaml"},
                                },
                            },
                        },
                    },
                    "files": {
                        "type": "object",
                        "properties": {
                            "configMapRef": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {"name": {"type": "string"}},
                            }
                        },
                    },
                    "execution": {
                        "type": "object",
                        "required": ["workers"],
                        "properties": {
                            "workers": {"type": "integer", "minimum": 1},
                            "controllerPort": {"type": "integer", "minimum": 1, "default": 5557},
                            "readyTimeoutSeconds": {"type": "number", "minimum": 0.1, "default": 60},
                        },
                    },
                    "clusterTokenSecretRef": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "key": {"type": "string", "default": "token"},
                        },
                    },
                    "artifacts": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean", "default": True},
                            "retain": {"type": "boolean", "default": True},
                            "storage": {
                                "type": "object",
                                "properties": {
                                    "mode": {"type": "string", "enum": ["pvc", "none"], "default": "pvc"},
                                    "size": {"type": "string", "default": "1Gi"},
                                },
                            },
                        },
                    },
                    "cleanupPolicy": {
                        "type": "string",
                        "enum": ["Retain", "DeleteOnSuccess", "DeleteAlways"],
                        "default": "DeleteOnSuccess",
                    },
                    "ttlSecondsAfterFinished": {"type": "integer", "minimum": 0},
                    "controller": _pod_customization_schema(),
                    "worker": _pod_customization_schema(),
                },
            },
            "status": {
                "type": "object",
                "x-kubernetes-preserve-unknown-fields": True,
            },
        },
    }


def _pod_customization_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "x-kubernetes-preserve-unknown-fields": True,
        "properties": {
            "env": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
            "envFrom": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
            "resources": {"type": "object", "x-kubernetes-preserve-unknown-fields": True},
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
            "annotations": {"type": "object", "additionalProperties": {"type": "string"}},
            "nodeSelector": {"type": "object", "additionalProperties": {"type": "string"}},
            "affinity": {"type": "object", "x-kubernetes-preserve-unknown-fields": True},
            "tolerations": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
            "topologySpreadConstraints": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
            "imagePullSecrets": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
            "serviceAccountName": {"type": "string"},
            "volumes": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
            "volumeMounts": {"type": "array", "x-kubernetes-preserve-unknown-fields": True},
        },
    }
