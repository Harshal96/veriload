"""Runtime entrypoint for the optional Kopf-based operator."""

from __future__ import annotations


def run_operator() -> None:
    """Run the VeriLoad Kubernetes Operator."""

    try:
        import kopf
        from kubernetes import client, config, dynamic
    except ImportError as exc:
        raise RuntimeError(
            "VeriLoad operator runtime requires the optional operator dependencies. "
            "Install with `pip install veriload[operator]` or `uv sync --extra operator`."
        ) from exc

    from veriload.k8s_operator.handlers import register_handlers

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    class _ClientModule:
        ApiClient = client.ApiClient
        DynamicClient = dynamic.DynamicClient

    register_handlers(kopf, _ClientModule)
    kopf.run()
