import subprocess

from flask import Flask, jsonify, request
from kubernetes import client, config

app = Flask(__name__)


@app.route("/")
def hello():
    return jsonify({"status": "ok", "message": "Integration testing service running"})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/vcluster", methods=["POST"])
def create_vcluster():
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' field in request body"}), 400

    cluster_name = data["name"]
    result = subprocess.run(
        [
            "vcluster",
            "create",
            cluster_name,
            "--connect=false",
            "-f",
            "/app/vcluster.yaml",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return jsonify({"error": result.stderr}), 500

    return jsonify({"message": f"vcluster {cluster_name} created successfully"}), 201


@app.route("/cluster-config/<cluster_name>", methods=["GET"])
def get_cluster_config(cluster_name):
    config.load_incluster_config()
    v1 = client.CoreV1Api()

    with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
        namespace = f.read().strip()

    secret_name = f"vc-{cluster_name}"
    try:
        secret = v1.read_namespaced_secret(name=secret_name, namespace=namespace)
        return jsonify(secret.to_dict()), 200
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return jsonify({"error": f"Secret {secret_name} not found"}), 404
        raise


@app.route("/vcluster/<cluster_name>", methods=["DELETE"])
def delete_vcluster(cluster_name):
    result = subprocess.run(
        ["vcluster", "delete", cluster_name], capture_output=True, text=True
    )

    if result.returncode != 0:
        return jsonify({"error": result.stderr}), 500

    return jsonify({"message": f"vcluster {cluster_name} deleted successfully"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
