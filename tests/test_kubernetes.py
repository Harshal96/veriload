import yaml

from veriload.kubernetes import generate_kubernetes_manifest


def test_generate_kubernetes_manifest_includes_configmap_controller_and_workers() -> None:
    manifest = generate_kubernetes_manifest(
        name="checkout-load",
        image="ghcr.io/acme/veriload:latest",
        config_text="run:\n  users: 10\n",
        workers=3,
    )

    docs = [doc for doc in yaml.safe_load_all(manifest) if doc]

    assert [doc["kind"] for doc in docs] == ["ConfigMap", "Job", "Job"]
    assert docs[0]["metadata"]["name"] == "checkout-load-config"
    assert docs[1]["metadata"]["name"] == "checkout-load-controller"
    assert docs[2]["metadata"]["name"] == "checkout-load-worker"
    assert docs[2]["spec"]["parallelism"] == 3
    assert "veriload.yaml" in docs[0]["data"]


def test_generate_kubernetes_manifest_rejects_invalid_worker_count() -> None:
    try:
        generate_kubernetes_manifest(
            name="bad",
            image="veriload:latest",
            config_text="run: {}",
            workers=0,
        )
    except ValueError as exc:
        assert "workers" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_generate_networked_kubernetes_manifest_uses_control_plane_resources() -> None:
    manifest = generate_kubernetes_manifest(
        name="checkout-load",
        image="ghcr.io/acme/veriload:latest",
        config_text="run:\n  users: 10\n",
        workers=3,
        networked=True,
        cluster_token_secret="checkout-load-token",
    )

    docs = [doc for doc in yaml.safe_load_all(manifest) if doc]

    assert [doc["kind"] for doc in docs] == ["ConfigMap", "Service", "Job", "Job"]
    assert docs[1]["metadata"]["name"] == "checkout-load-controller"
    assert docs[2]["spec"]["template"]["spec"]["containers"][0]["args"][0:2] == [
        "distributed",
        "controller",
    ]
    assert docs[3]["spec"]["parallelism"] == 3
    assert docs[3]["spec"]["template"]["spec"]["containers"][0]["args"][0:2] == [
        "distributed",
        "worker",
    ]
    assert docs[3]["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["name"] == (
        "checkout-load-token"
    )
