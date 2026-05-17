"""Kubernetes manifest generation for distributed VeriLoad runs."""

from __future__ import annotations

import yaml


def generate_kubernetes_manifest(
    *,
    name: str,
    image: str,
    config_text: str,
    workers: int,
    networked: bool = False,
    cluster_token_secret: str = "veriload-cluster-token",
    controller_port: int = 5557,
) -> str:
    """Generate a controller/worker Kubernetes Job manifest."""

    if workers <= 0:
        raise ValueError("workers must be greater than 0")
    config_name = f"{name}-config"
    if networked:
        docs = [
            _config_map(config_name, config_text),
            _service(f"{name}-controller", controller_port),
            _job(
                name=f"{name}-controller",
                image=image,
                config_name=config_name,
                args=[
                    "distributed",
                    "controller",
                    "--config",
                    "/etc/veriload/veriload.yaml",
                    "--bind-host",
                    "0.0.0.0",
                    "--bind-port",
                    str(controller_port),
                    "--expect-workers",
                    str(workers),
                    "--cluster-token-env",
                    "VERILOAD_CLUSTER_TOKEN",
                ],
                parallelism=1,
                env=_cluster_token_env(cluster_token_secret),
            ),
            _job(
                name=f"{name}-worker",
                image=image,
                config_name=None,
                args=[
                    "distributed",
                    "worker",
                    "--controller-host",
                    f"{name}-controller",
                    "--controller-port",
                    str(controller_port),
                    "--cluster-token-env",
                    "VERILOAD_CLUSTER_TOKEN",
                ],
                parallelism=workers,
                env=_cluster_token_env(cluster_token_secret),
            ),
        ]
        return "---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs)
    docs = [
        _config_map(config_name, config_text),
        _job(
            name=f"{name}-controller",
            image=image,
            config_name=config_name,
            args=["run", "--config", "/etc/veriload/veriload.yaml", "--workers", str(workers)],
            parallelism=1,
        ),
        _job(
            name=f"{name}-worker",
            image=image,
            config_name=config_name,
            args=["run", "--config", "/etc/veriload/veriload.yaml"],
            parallelism=workers,
        ),
    ]
    return "---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs)


def _config_map(name: str, config_text: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name},
        "data": {"veriload.yaml": config_text},
    }


def _service(name: str, port: int) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name},
        "spec": {
            "selector": {"app.kubernetes.io/name": "veriload", "job": name},
            "ports": [{"name": "control", "port": port, "targetPort": port}],
        },
    }


def _job(
    *,
    name: str,
    image: str,
    config_name: str | None,
    args: list[str],
    parallelism: int,
    env: list[dict] | None = None,
) -> dict:
    container = {
        "name": "veriload",
        "image": image,
        "args": args,
        "env": [*(env or ()), {"name": "PYTHONUNBUFFERED", "value": "1"}],
    }
    volumes = []
    if config_name is not None:
        container["volumeMounts"] = [
            {
                "name": "config",
                "mountPath": "/etc/veriload",
                "readOnly": True,
            }
        ]
        volumes.append(
            {
                "name": "config",
                "configMap": {"name": config_name},
            }
        )
    template_spec = {
        "restartPolicy": "Never",
        "containers": [container],
    }
    if volumes:
        template_spec["volumes"] = volumes
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name},
        "spec": {
            "parallelism": parallelism,
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/name": "veriload", "job": name}},
                "spec": template_spec,
            },
        },
    }


def _cluster_token_env(secret_name: str) -> list[dict]:
    return [
        {
            "name": "VERILOAD_CLUSTER_TOKEN",
            "valueFrom": {
                "secretKeyRef": {
                    "name": secret_name,
                    "key": "token",
                }
            },
        }
    ]
