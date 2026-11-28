"""Trusted disposable-runner bootstrap and Argo reconciliation checks."""

import json
from pathlib import Path
import subprocess
import sys
import time


NS = "aao-lab"
ARGO = "argocd"
NAME = "adaptive-agentops"
NODE = "aao-stage6-control-plane"
IMAGE = "adaptive-agentops-stage6:local"
GIT_CONTAINER = "aao-stage6-git"
ARGO_REV = "b4accc482028fd529b3e4508ef9f28977b552b50"
SERVER = "https://kubernetes.default.svc:443"
ACCOUNT = "aao-reconciler"
SOURCE = Path("deploy/stage6/workload/deployment.json")
VOLUMES = {
    "proposal": (60102, 60111, "0750"),
    "operator": (60102, 60113, "0750"),
    "target": (60103, 60112, "0750"),
    "inspection": (60102, 60116, "0750"),
    "controller-state": (60102, 60102, "0700"),
    "target-state": (60103, 60103, "0700"),
    "audit-state": (60102, 60102, "0700"),
}


def run(*args, payload=None, check=True, timeout=180):
    result = subprocess.run(args, input=payload, check=False, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {(result.stderr or result.stdout)[-2500:]}")
    return result


def k(*args, **kwargs):
    return run("kubectl", "--request-timeout=160s", *args, **kwargs)


def resource(api, kind, name, namespace=None, **fields):
    metadata = {"name": name}
    if namespace is not None:
        metadata["namespace"] = namespace
    return {"apiVersion": api, "kind": kind, "metadata": metadata, **fields}


def apply(*objects):
    k("apply", "-f", "-", payload=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))


def get(kind, name, namespace=NS):
    return json.loads(k("-n", namespace, "get", kind, name, "-o", "json").stdout)


def wait_for(label, condition, seconds=150):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := condition():
            return result
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {label}")


def provision():
    namespace = resource("v1", "Namespace", NS)
    namespace["metadata"]["labels"] = {
        "pod-security.kubernetes.io/enforce": "restricted",
        "pod-security.kubernetes.io/enforce-version": "v1.37",
    }
    apply(namespace, resource("v1", "Namespace", ARGO))
    for name, (uid, gid, mode) in VOLUMES.items():
        path = f"/var/lib/aao-lab/{name}"
        run("docker", "exec", NODE, "install", "-d", "-m", mode, "-o", str(uid), "-g", str(gid), path)
        pv = resource("v1", "PersistentVolume", f"aao-{name}", spec={
            "capacity": {"storage": "32Mi"}, "accessModes": ["ReadWriteOnce"],
            "storageClassName": "aao-local", "persistentVolumeReclaimPolicy": "Retain",
            "local": {"path": path}, "claimRef": {"namespace": NS, "name": name},
            "nodeAffinity": {"required": {"nodeSelectorTerms": [{"matchExpressions": [
                {"key": "kubernetes.io/hostname", "operator": "In", "values": [NODE]},
            ]}]}},
        })
        pvc = resource("v1", "PersistentVolumeClaim", name, NS, spec={
            "accessModes": ["ReadWriteOnce"], "storageClassName": "aao-local",
            "volumeName": f"aao-{name}", "resources": {"requests": {"storage": "32Mi"}},
        })
        apply(pv, pvc)


def reconcile_role(allow_create):
    rules = [
        {"apiGroups": ["apps"], "resources": ["deployments", "replicasets"],
         "verbs": ["get", "list", "watch"]},
        {"apiGroups": [""], "resources": ["pods", "events"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": [NAME],
         "verbs": ["patch", "update"]},
    ]
    if allow_create:
        rules.append({"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["create"]})
    apply(resource("rbac.authorization.k8s.io/v1", "Role", ACCOUNT, NS, rules=rules))


def admission():
    api = "admissionregistration.k8s.io/v1"
    apply(resource(api, "ValidatingAdmissionPolicy", "aao-deployment-lock", spec={
        "failurePolicy": "Fail",
        "matchConstraints": {"resourceRules": [{
            "apiGroups": ["apps"], "apiVersions": ["v1"], "operations": ["UPDATE"],
            "resources": ["deployments"],
        }]},
        "validations": [{"expression": "object.spec == oldObject.spec",
                         "message": "AAO Deployment spec is frozen"}],
    }), resource(api, "ValidatingAdmissionPolicyBinding", "aao-deployment-lock", spec={
        "policyName": "aao-deployment-lock", "validationActions": ["Deny"],
        "matchResources": {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NS}}},
    }))


def argo(install, repo, revision):
    k("apply", "--server-side", "-k", f"https://github.com/argoproj/argo-cd/manifests/crds?ref={ARGO_REV}")
    k("-n", ARGO, "apply", "--server-side", "-f", install)
    for name in ("argocd-server", "argocd-dex-server", "argocd-notifications-controller",
                 "argocd-applicationset-controller"):
        k("-n", ARGO, "scale", "deployment", name, "--replicas=0")
    k("-n", ARGO, "patch", "configmap", "argocd-cm", "--type=merge", "-p", json.dumps({"data": {
        "cluster.inClusterEnabled": "false",
        "timeout.reconciliation": "5s",
        "timeout.reconciliation.jitter": "0s",
        "resource.inclusions": "- apiGroups: [apps]\n  kinds: [Deployment, ReplicaSet]\n  clusters: ['*']\n"
                               "- apiGroups: ['']\n  kinds: [Pod, Event]\n  clusters: ['*']\n",
    }}))
    for kind, name in (("deployment", "argocd-repo-server"), ("statefulset", "argocd-application-controller")):
        k("-n", ARGO, "rollout", "status", f"{kind}/{name}", "--timeout=150s")

    apply(resource("v1", "ServiceAccount", ACCOUNT, NS, automountServiceAccountToken=False),
          resource("rbac.authorization.k8s.io/v1", "RoleBinding", ACCOUNT, NS,
                   roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": ACCOUNT},
                   subjects=[{"kind": "ServiceAccount", "name": ACCOUNT, "namespace": NS}]))
    reconcile_role(True)
    token = k("-n", NS, "create", "token", ACCOUNT, "--duration=30m").stdout.strip()
    kubeconfig = json.loads(k("config", "view", "--raw", "--minify", "-o", "json").stdout)
    ca_data = kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"]
    secret = resource("v1", "Secret", "aao-destination", ARGO, stringData={
        "name": "aao-destination", "server": SERVER, "namespaces": NS, "clusterResources": "false",
        "project": "aao",
        "config": json.dumps({"bearerToken": token, "tlsClientConfig": {"insecure": False, "caData": ca_data}}),
    })
    secret["metadata"]["labels"] = {"argocd.argoproj.io/secret-type": "cluster"}
    # Creating from stdin avoids logging or storing the temporary credential in the worktree.
    if k("create", "-f", "-", payload=json.dumps(secret), check=False).returncode:
        raise RuntimeError("Could not register scoped Argo destination")
    api = "argoproj.io/v1alpha1"
    apply(resource(api, "AppProject", "aao", ARGO, spec={
        "sourceRepos": [repo], "destinations": [{"server": SERVER, "namespace": NS}],
        "clusterResourceWhitelist": [],
        "namespaceResourceWhitelist": [{"group": "apps", "kind": "Deployment"}],
    }), resource(api, "Application", "aao", ARGO, spec={
        "project": "aao", "source": {"repoURL": repo, "path": "workload", "targetRevision": revision},
        "destination": {"server": SERVER, "namespace": NS},
        "syncPolicy": {"automated": {"prune": False, "selfHeal": True}},
    }))


class GitSource:
    def __init__(self, path):
        self.path = Path(path)
        self.root = self.path / "source"
        self.bare = self.path / "served/lab.git"
        self.document = json.loads(SOURCE.read_text())
        self.root.mkdir(parents=True)
        (self.root / "workload").mkdir()
        self.bare.parent.mkdir()
        run("git", "init", "-b", "lab", str(self.root))
        run("git", "init", "--bare", "-b", "lab", str(self.bare))

    def commit(self, note, prohibited=False):
        document = json.loads(json.dumps(self.document))
        document["metadata"]["annotations"]["aao.example/lab-note"] = note
        if prohibited:
            document["spec"]["template"]["spec"]["containers"][0]["securityContext"]["privileged"] = True
        (self.root / "workload/deployment.json").write_text(json.dumps(document))
        args = ("git", "-C", str(self.root))
        run(*args, "add", "workload/deployment.json")
        run(*args, "-c", "user.name=Disposable lab", "-c", "user.email=lab@example.invalid", "commit", "-m", note)
        run(*args, "push", str(self.bare), "lab")
        return run(*args, "rev-parse", "HEAD").stdout.strip()

    def serve(self):
        run("docker", "run", "-d", "--name", GIT_CONTAINER, "--network", "kind", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--user=65534:65534",
            "--mount", f"type=bind,source={self.bare.parent},target=/served,readonly", IMAGE,
            "git", "-c", "safe.directory=/served/lab.git", "daemon", "--reuseaddr", "--listen=0.0.0.0", "--port=9418", "--base-path=/served",
            "--export-all", "--strict-paths", "--disable=receive-pack", "--forbid-override=receive-pack",
            "/served/lab.git")
        address = run("docker", "inspect", "--format", "{{.NetworkSettings.Networks.kind.IPAddress}}",
                      GIT_CONTAINER).stdout.strip()
        return f"git://{address}:9418/lab.git"


def observe(revision, note):
    def synced():
        app = get("application", "aao", ARGO).get("status", {})
        if app.get("sync", {}).get("revision") != revision or app["sync"]["status"] != "Synced":
            return False
        deployment = get("deployment", NAME, NS)
        return (deployment["metadata"]["annotations"].get("aao.example/lab-note") == note)
    try:
        wait_for(f"Argo Synced at {revision} with note={note}", synced)
    except RuntimeError:
        status = get("application", "aao", ARGO).get("status", {})
        print(json.dumps({field: status.get(field) for field in ("sync", "conditions", "operationState")}),
              flush=True)
        raise
    print(f"Argo Synced {revision} lab-note={note}", flush=True)


def require_forbidden(account, verb, kind, *flags):
    principal = f"system:serviceaccount:{NS}:{account}"
    result = k("-n", NS, "--as", principal, "--as-group=system:authenticated",
               "--as-group=system:serviceaccounts", f"--as-group=system:serviceaccounts:{NS}",
               "auth", "can-i", verb, kind, *flags, check=False)
    if result.returncode != 1 or result.stdout.strip() != "no":
        raise RuntimeError(f"Unexpected {account} authority for {verb} {kind} {flags}")


def verify_rbac():
    for verb, kind in (("create", "deployments"), ("delete", "deployments"),
                       ("patch", "persistentvolumeclaims"), ("create", "rolebindings"), ("get", "secrets")):
        require_forbidden(ACCOUNT, verb, kind)
    require_forbidden(ACCOUNT, "create", "pods", "--subresource=exec")
    for verb, kind in (("get", "secrets"), ("create", "pods"), ("patch", "deployments")):
        require_forbidden("default", verb, kind)


def main():
    from stage6_k8s_probe import exercise, verify_missing_state

    directory, install = sys.argv[1:]
    provision()
    admission()
    source = GitSource(directory)
    initial = source.commit("initial")
    repo = source.serve()
    argo(install, repo, initial)
    observe(initial, "initial")
    reconcile_role(False)
    verify_rbac()
    k("-n", ARGO, "patch", "application", "aao", "--type=merge", "-p",
      '{"spec":{"source":{"targetRevision":"lab"}}}')
    print("PASS Argo initial Git synchronization and scoped ongoing RBAC", flush=True)
    fingerprint = exercise()

    changed = source.commit("changed")
    observe(changed, "changed")
    print("PASS Argo reconciled a new Git commit", flush=True)

    response = k("-n", NS, "annotate", "deployment", NAME, "aao.example/lab-note=live-drift", "--overwrite",
                 "-o", "json")
    if json.loads(response.stdout)["metadata"]["annotations"]["aao.example/lab-note"] != "live-drift":
        raise RuntimeError("Live drift was not written")
    observe(changed, "changed")
    print("PASS Argo corrected observed live drift", flush=True)

    denied = source.commit("prohibited", prohibited=True)
    def rejected():
        operation = get("application", "aao", ARGO).get("status", {}).get("operationState", {})
        return (operation.get("syncResult", {}).get("revision") == denied and
                operation.get("phase") in {"Error", "Failed"} and
                "AAO Deployment spec is frozen" in json.dumps(operation))
    wait_for("admission denial of prohibited Git revision", rejected)
    print(f"PASS Argo prohibited Git revision {denied} rejected by admission", flush=True)
    restored = source.commit("restored")
    observe(restored, "restored")
    if exercise(snapshot_only=True) != fingerprint:
        raise RuntimeError("GitOps activity altered retained application state")
    print("PASS reconciliation preserved durable state and Pod identity", flush=True)
    verify_missing_state()


if __name__ == "__main__":
    # The probe imports this module for the same helpers.
    sys.modules["stage6_k8s_lab"] = sys.modules[__name__]
    main()
