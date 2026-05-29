"""
Hermes infra CLI — manage per-profile k3d cluster resources.

Provides ``hermes infra up|down|status`` for lifecycle management of
Hermes profile namespaces (GitNexus MCP, Prefect worker, resource quotas).

The manifests are generated from templates and applied via kubectl.
Flux-managed namespaces (cluster-bootstrap kustomization) are detected
and handled with care — ``down`` scales resources to zero rather than
deleting the namespace when Flux owns it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import Optional

# ── Profile registry ───────────────────────────────────────────────────
# Mirrors parent task artifacts:
#   t_323e361c — namespace + quota + network-policy manifests
#   t_f5ccc732 — GitNexus MCP per-namespace manifests
#   t_19be5e5d — Prefect per-namespace isolation

PROFILES: dict[str, dict] = {
    "default":  {"port": 8090, "cpu_req": "2", "cpu_lim": "4", "mem_req": "4Gi", "mem_lim": "8Gi", "storage": "10Gi"},
    "engineer": {"port": 8091, "cpu_req": "4", "cpu_lim": "8", "mem_req": "8Gi", "mem_lim": "16Gi", "storage": "20Gi"},
    "cto":      {"port": 8092, "cpu_req": "2", "cpu_lim": "4", "mem_req": "4Gi", "mem_lim": "8Gi", "storage": "10Gi"},
    "personal": {"port": 8093, "cpu_req": "1", "cpu_lim": "2", "mem_req": "2Gi", "mem_lim": "4Gi", "storage": "5Gi"},
    "sre":      {"port": 8094, "cpu_req": "2", "cpu_lim": "4", "mem_req": "4Gi", "mem_lim": "8Gi", "storage": "10Gi"},
    "bmad":     {"port": 8095, "cpu_req": "2", "cpu_lim": "4", "mem_req": "4Gi", "mem_lim": "8Gi", "storage": "10Gi"},
}

HERMES_NS_PREFIX = "hermes-"

# ── kubectl helpers ────────────────────────────────────────────────────


def _kubectl(args: list[str], check: bool = True, capture: bool = True) -> tuple[int, str, str]:
    """Run kubectl and return (exit_code, stdout, stderr)."""
    cmd = ["kubectl"] + args
    proc = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=60,
    )
    if check and proc.returncode != 0:
        print(f"[ERROR] kubectl {' '.join(args)}", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _kubectl_apply(manifest: str) -> bool:
    """Apply a manifest from stdin. Returns True on success."""
    proc = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        print(f"[ERROR] kubectl apply: {proc.stderr}", file=sys.stderr)
        return False
    if proc.stdout:
        print(proc.stdout.strip())
    return True


def _kubectl_delete(manifest: str) -> bool:
    """Delete resources from a manifest. Returns True on success."""
    proc = subprocess.run(
        ["kubectl", "delete", "-f", "-", "--ignore-not-found"],
        input=manifest,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        print(f"[ERROR] kubectl delete: {proc.stderr}", file=sys.stderr)
        return False
    if proc.stdout:
        print(proc.stdout.strip())
    return True


def _namespace_exists(name: str) -> bool:
    """Check if a namespace exists."""
    code, _, _ = _kubectl(["get", "ns", name], check=False)
    return code == 0


def _is_flux_managed(namespace: str) -> bool:
    """Check if namespace has Flux kustomization labels."""
    code, stdout, _ = _kubectl(
        ["get", "ns", namespace, "-o", "jsonpath={.metadata.labels}"],
        check=False,
    )
    if code != 0:
        return False
    return "kustomize.toolkit.fluxcd.io/name" in stdout


# ── Manifest generators ────────────────────────────────────────────────


def _namespace_manifest(profile: str) -> str:
    """Generate Namespace manifest."""
    ns = f"{HERMES_NS_PREFIX}{profile}"
    return textwrap.dedent(f"""\
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {ns}
          labels:
            app.kubernetes.io/part-of: hermes-infra
            hermes-infra/profile: "{profile}"
    """)


def _resourcequota_manifest(profile: str) -> str:
    """Generate ResourceQuota manifest from profile config."""
    cfg = PROFILES[profile]
    ns = f"{HERMES_NS_PREFIX}{profile}"
    return textwrap.dedent(f"""\
        apiVersion: v1
        kind: ResourceQuota
        metadata:
          name: {ns}-quota
          namespace: {ns}
        spec:
          hard:
            requests.cpu: "{cfg['cpu_req']}"
            requests.memory: "{cfg['mem_req']}"
            limits.cpu: "{cfg['cpu_lim']}"
            limits.memory: "{cfg['mem_lim']}"
            persistentvolumeclaims: "10"
            requests.storage: "{cfg['storage']}"
    """)


def _gitnexus_manifest(profile: str) -> str:
    """Generate GitNexus MCP deployment manifests for a profile.

    Port: 8090-8095 per profile.  SOCAT bridges stdio→TCP for MCP transport.
    PVC: 5Gi for index persistence.  CronJob for staggered re-indexing.
    """
    cfg = PROFILES[profile]
    ns = f"{HERMES_NS_PREFIX}{profile}"
    port = cfg["port"]
    # Staggered cron: one profile per minute 0-5
    cron_minute = ["default", "engineer", "cto", "personal", "sre", "bmad"].index(profile)

    return textwrap.dedent(f"""\
        ---
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: gitnexus-config
          namespace: {ns}
        data:
          GITNEXUS_PORT: "{port}"
          GITNEXUS_BIND: "127.0.0.1"
        ---
        apiVersion: v1
        kind: PersistentVolumeClaim
        metadata:
          name: gitnexus-index
          namespace: {ns}
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: 5Gi
        ---
        apiVersion: v1
        kind: Service
        metadata:
          name: gitnexus
          namespace: {ns}
        spec:
          selector:
            app: gitnexus
          ports:
            - port: {port}
              targetPort: {port}
              protocol: TCP
              name: mcp
        ---
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: gitnexus
          namespace: {ns}
          labels:
            app: gitnexus
            hermes-infra/component: mcp
        spec:
          replicas: 1
          selector:
            matchLabels:
              app: gitnexus
          template:
            metadata:
              labels:
                app: gitnexus
            spec:
              serviceAccountName: {ns}-sa
              containers:
                - name: gitnexus
                  image: node:22-alpine
                  command: ["sh", "-c"]
                  args:
                    - |
                      apk add --no-cache socat
                      socat TCP-LISTEN:{port},reuseaddr,fork EXEC:"node /app/index.js"
                  ports:
                    - containerPort: {port}
                      protocol: TCP
                  volumeMounts:
                    - name: index-data
                      mountPath: /app/data
                  resources:
                    requests:
                      cpu: "250m"
                      memory: "512Mi"
                    limits:
                      cpu: "1"
                      memory: "2Gi"
              volumes:
                - name: index-data
                  persistentVolumeClaim:
                    claimName: gitnexus-index
        ---
        apiVersion: batch/v1
        kind: CronJob
        metadata:
          name: gitnexus-reindex
          namespace: {ns}
        spec:
          schedule: "{cron_minute} */6 * * *"
          concurrencyPolicy: Forbid
          jobTemplate:
            spec:
              template:
                spec:
                  serviceAccountName: {ns}-sa
                  containers:
                    - name: reindex
                      image: node:22-alpine
                      command: ["sh", "-c"]
                      args:
                        - "echo 'Reindex triggered'; exit 0"
                  restartPolicy: OnFailure
    """)


def _networkpolicy_manifest(profile: str) -> str:
    """Generate NetworkPolicy — allow intra-ns, DNS, and egress; deny cross-ns ingress."""
    ns = f"{HERMES_NS_PREFIX}{profile}"
    return textwrap.dedent(f"""\
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: {ns}-netpol
          namespace: {ns}
        spec:
          podSelector: {{}}
          policyTypes:
            - Ingress
            - Egress
          ingress:
            - from:
                - namespaceSelector:
                    matchLabels:
                      kubernetes.io/metadata.name: {ns}
            - from:
                - namespaceSelector:
                    matchLabels:
                      kubernetes.io/metadata.name: kube-system
                  podSelector:
                    matchLabels:
                      app.kubernetes.io/name: traefik
          egress:
            - to:
                - namespaceSelector: {{}}
                - podSelector:
                    matchLabels:
                      k8s-app: kube-dns
              ports:
                - port: 53
                  protocol: UDP
                - port: 53
                  protocol: TCP
            - to:
                - ipBlock:
                    cidr: 0.0.0.0/0
                    except:
                      - 10.0.0.0/8
                      - 172.16.0.0/12
                      - 192.168.0.0/16
    """)


def _serviceaccount_manifest(profile: str) -> str:
    """Generate ServiceAccount manifest."""
    ns = f"{HERMES_NS_PREFIX}{profile}"
    return textwrap.dedent(f"""\
        apiVersion: v1
        kind: ServiceAccount
        metadata:
          name: {ns}-sa
          namespace: {ns}
    """)


def _build_component_manifests(profile: str) -> str:
    """Concatenate non-namespace manifests for a profile (namespace must exist first)."""
    parts = [
        _serviceaccount_manifest(profile),
        _resourcequota_manifest(profile),
        _networkpolicy_manifest(profile),
        _gitnexus_manifest(profile),
    ]
    return "---\n".join(parts)


def _build_all_manifests(profile: str) -> str:
    """Concatenate ALL manifests including namespace (use with caution — prefer two-phase)."""
    parts = [
        _namespace_manifest(profile),
        _serviceaccount_manifest(profile),
        _resourcequota_manifest(profile),
        _networkpolicy_manifest(profile),
        _gitnexus_manifest(profile),
    ]
    return "---\n".join(parts)


# ── Command implementations ─────────────────────────────────────────────


def infra_up(profile: str) -> int:
    """Bring up infrastructure for a profile namespace.

    1. Create namespace if missing
    2. Apply all component manifests
    3. Report status
    """
    if profile not in PROFILES:
        print(f"Error: unknown profile '{profile}'. Available: {', '.join(PROFILES)}", file=sys.stderr)
        return 1

    ns = f"{HERMES_NS_PREFIX}{profile}"
    print(f"[infra] Bringing up {ns} ...")

    # 1. Create namespace first (separate from component manifests)
    exists = _namespace_exists(ns)
    if not exists:
        ns_manifest = _namespace_manifest(profile)
        if not _kubectl_apply(ns_manifest):
            return 1
        # Wait briefly for namespace to be active
        subprocess.run(
            ["kubectl", "wait", "--for=condition=Active", f"ns/{ns}", "--timeout=10s"],
            capture_output=True, text=True, timeout=15,
        )
        print(f"[infra] Namespace {ns} created.")
    else:
        print(f"[infra] Namespace {ns} already exists. Applying components...")

    # 2. Apply component manifests
    manifests = _build_component_manifests(profile)
    if not _kubectl_apply(manifests):
        return 1

    # 4. Show status
    print(f"\n[infra] {ns} status:")
    return infra_status_for_profile(profile)


def infra_down(profile: str) -> int:
    """Tear down infrastructure for a profile namespace.

    If Flux-managed: scale deployments to 0 (don't delete namespace)
    Otherwise: delete all our resources, optionally the namespace.
    """
    if profile not in PROFILES:
        print(f"Error: unknown profile '{profile}'. Available: {', '.join(PROFILES)}", file=sys.stderr)
        return 1

    ns = f"{HERMES_NS_PREFIX}{profile}"
    print(f"[infra] Tearing down {ns} ...")

    if not _namespace_exists(ns):
        print(f"[infra] Namespace {ns} does not exist — nothing to tear down.")
        return 0

    flux_managed = _is_flux_managed(ns)

    if flux_managed:
        print(f"[infra] {ns} is Flux-managed — scaling deployments to 0 (not deleting namespace).")
        # Scale down all deployments in the namespace
        code, stdout, stderr = _kubectl(
            ["get", "deploy", "-n", ns, "-o", "jsonpath={.items[*].metadata.name}"],
            check=False,
        )
        if code == 0 and stdout.strip():
            for deploy in stdout.split():
                print(f"  Scaling {deploy} to 0...")
                _kubectl(["scale", "deployment", deploy, "-n", ns, "--replicas=0"], check=False)
        # Also scale down statefulsets
        code, stdout, stderr = _kubectl(
            ["get", "sts", "-n", ns, "-o", "jsonpath={.items[*].metadata.name}"],
            check=False,
        )
        if code == 0 and stdout.strip():
            for sts in stdout.split():
                print(f"  Scaling {sts} to 0...")
                _kubectl(["scale", "sts", sts, "-n", ns, "--replicas=0"], check=False)
                print(f"[infra] {ns} scaled down (Flux-owned namespace preserved).")
    else:
        # Delete PVCs first to avoid hangs, then delete the namespace
        code, stdout, _ = _kubectl(
            ["get", "pvc", "-n", ns, "-o", "jsonpath={.items[*].metadata.name}"],
            check=False,
        )
        if code == 0 and stdout.strip():
            for pvc in stdout.split():
                print(f"  Deleting PVC {pvc}...")
                _kubectl(["delete", "pvc", pvc, "-n", ns, "--ignore-not-found", "--wait=false"], check=False)

        # Delete the namespace (cleans up everything inside)
        print(f"  Deleting namespace {ns}...")
        _kubectl(["delete", "ns", ns, "--ignore-not-found", "--wait=false"], check=False)

    print(f"[infra] {ns} torn down.")
    return 0


def infra_status() -> int:
    """Show status of all hermes-* namespaces and their components."""
    print("=" * 72)
    print(f"{'NAMESPACE':<20} {'PODS':>6} {'READY':>6} {'STATUS':<12} {'FLUX':<6}")
    print("=" * 72)

    all_healthy = True
    for profile in PROFILES:
        ns = f"{HERMES_NS_PREFIX}{profile}"
        if not _namespace_exists(ns):
            print(f"{ns:<20} {'—':>6} {'—':>6} {'MISSING':<12} {'—':<6}")
            all_healthy = False
            continue

        flux = "✓" if _is_flux_managed(ns) else "—"

        # Count pods
        code, stdout, _ = _kubectl(
            ["get", "pods", "-n", ns, "-o", "jsonpath={.items[*].status.phase}"],
            check=False,
        )
        if code != 0:
            print(f"{ns:<20} {'ERROR':>6} {'—':>6} {'ERROR':<12} {flux:<6}")
            all_healthy = False
            continue

        phases = stdout.split() if stdout.strip() else []
        total = len(phases)
        ready = sum(1 for p in phases if p in ("Running", "Succeeded"))
        if total == 0:
            status = "EMPTY"
            all_healthy = False
        elif ready == total and all(p in ("Running", "Succeeded") for p in phases):
            status = "HEALTHY"
        else:
            status = "DEGRADED"
            all_healthy = False

        print(f"{ns:<20} {total:>6} {ready:>6} {status:<12} {flux:<6}")

    print("=" * 72)

    # Component detail for active namespaces
    print()
    for profile in PROFILES:
        ns = f"{HERMES_NS_PREFIX}{profile}"
        if not _namespace_exists(ns):
            continue

        code, stdout, _ = _kubectl(
            ["get", "deploy", "-n", ns, "-o", "jsonpath={range .items[*]}{.metadata.name}={.status.readyReplicas}/{.spec.replicas} {end}"],
            check=False,
        )
        if code == 0 and stdout.strip():
            print(f"  {ns} deployments: {stdout.strip()}")

    return 0 if all_healthy else 1


def get_infra_status() -> dict:
    """Return infrastructure status as a JSON-serializable dict.

    Designed for programmatic consumers (profile discovery TUI, Prefect flows,
    health checks).  Mirrors ``infra_status()`` but returns a dict instead of
    printing.
    """
    namespaces = {}
    for profile, cfg in PROFILES.items():
        ns = f"{HERMES_NS_PREFIX}{profile}"
        entry: dict = {
            "profile": profile,
            "namespace": ns,
            "port": cfg["port"],
            "exists": False,
            "flux_managed": False,
            "pods": {"total": 0, "ready": 0, "phases": []},
            "deployments": {},
            "status": "MISSING",
        }

        if not _namespace_exists(ns):
            namespaces[ns] = entry
            continue

        entry["exists"] = True
        entry["flux_managed"] = _is_flux_managed(ns)

        # Pod phases
        code, stdout, _ = _kubectl(
            ["get", "pods", "-n", ns, "-o", "json"],
            check=False,
        )
        if code == 0 and stdout.strip():
            try:
                pods_data = json.loads(stdout)
                items = pods_data.get("items", [])
                entry["pods"]["total"] = len(items)
                ready_count = 0
                phases_list = []
                for pod in items:
                    phase = pod.get("status", {}).get("phase", "Unknown")
                    phases_list.append(phase)
                    if phase in ("Running", "Succeeded"):
                        ready_count += 1
                entry["pods"]["ready"] = ready_count
                entry["pods"]["phases"] = phases_list
                if ready_count == entry["pods"]["total"] and all(
                    p in ("Running", "Succeeded") for p in phases_list
                ) and entry["pods"]["total"] > 0:
                    entry["status"] = "HEALTHY"
                elif entry["pods"]["total"] > 0:
                    entry["status"] = "DEGRADED"
                else:
                    entry["status"] = "EMPTY"
            except (json.JSONDecodeError, KeyError):
                entry["status"] = "ERROR"

        # Deployments
        code, stdout, _ = _kubectl(
            ["get", "deploy", "-n", ns, "-o", "json"],
            check=False,
        )
        if code == 0 and stdout.strip():
            try:
                deploys_data = json.loads(stdout)
                items = deploys_data.get("items", [])
                for deploy in items:
                    name = deploy.get("metadata", {}).get("name", "unknown")
                    replicas = deploy.get("spec", {}).get("replicas", 0)
                    ready = deploy.get("status", {}).get("readyReplicas", 0)
                    entry["deployments"][name] = f"{ready}/{replicas}"
            except (json.JSONDecodeError, KeyError):
                pass

        namespaces[ns] = entry

    return {
        "profiles": PROFILES,
        "namespaces": namespaces,
        "all_healthy": all(ns["status"] == "HEALTHY" for ns in namespaces.values()),
    }


def infra_status_for_profile(profile: str, print_header: bool = True) -> int:
    """Show status for a single profile namespace. Returns 0 if healthy, 1 otherwise."""
    ns = f"{HERMES_NS_PREFIX}{profile}"

    if not _namespace_exists(ns):
        print(f"  Namespace {ns}: MISSING")
        return 1

    # Pods
    code, stdout, _ = _kubectl(
        ["get", "pods", "-n", ns, "-o", "wide"],
        check=False,
    )
    if code == 0:
        if print_header:
            print(f"  {ns} pods:")
        for line in stdout.split("\n")[1:]:  # skip header
            if line.strip():
                print(f"    {line}")

    # Deployments
    code, stdout, _ = _kubectl(
        ["get", "deploy", "-n", ns],
        check=False,
    )
    if code == 0:
        if print_header:
            print(f"  {ns} deployments:")
        for line in stdout.split("\n")[1:]:
            if line.strip():
                print(f"    {line}")

    return 0


# ── CLI entry point ─────────────────────────────────────────────────────


def cmd_infra(args) -> int:
    """Main entry point for `hermes infra`."""
    subcommand = getattr(args, "infra_command", None)

    if subcommand == "up":
        profile = getattr(args, "profile", None)
        if not profile:
            print("Error: profile name is required for `hermes infra up`", file=sys.stderr)
            print(f"Usage: hermes infra up <profile>", file=sys.stderr)
            print(f"Available profiles: {', '.join(PROFILES)}", file=sys.stderr)
            return 1
        return infra_up(profile)

    elif subcommand == "down":
        profile = getattr(args, "profile", None)
        if not profile:
            print("Error: profile name is required for `hermes infra down`", file=sys.stderr)
            print(f"Usage: hermes infra down <profile>", file=sys.stderr)
            print(f"Available profiles: {', '.join(PROFILES)}", file=sys.stderr)
            return 1
        return infra_down(profile)

    elif subcommand == "status":
        profile = getattr(args, "profile", None)
        if profile:
            if profile not in PROFILES:
                print(f"Error: unknown profile '{profile}'. Available: {', '.join(PROFILES)}", file=sys.stderr)
                return 1
            return infra_status_for_profile(profile)
        return infra_status()

    elif subcommand == "auto-start":
        profile = getattr(args, "profile", None)
        if not profile:
            print("Error: profile name is required for `hermes infra auto-start`", file=sys.stderr)
            return 1
        ok = auto_start(profile)
        return 0 if ok else 1

    elif subcommand == "idle-teardown":
        hours = getattr(args, "hours", 4)
        scaled = idle_teardown(idle_hours=hours)
        return 0 if scaled >= 0 else 1

    else:
        # No subcommand — show usage
        print("Usage: hermes infra <up|down|status|auto-start|idle-teardown>")
        print()
        print("Commands:")
        print("  up            <profile>   Bring up infrastructure for a profile")
        print("  down          <profile>   Tear down infrastructure for a profile")
        print("  status        [profile]   Show infrastructure status")
        print("  auto-start    <profile>   Ensure infra is up (session hook)")
        print("  idle-teardown [--hours N] Scale down non-critical pods in idle namespaces")
        print()
        print(f"Profiles: {', '.join(PROFILES)}")
        return 0


# ── Auto-start & idle-teardown ──────────────────────────────────────────


def auto_start(profile: str) -> bool:
    """Check if infrastructure exists for a profile and bring it up if missing.

    Designed to be called at session startup. Returns True if infra was
    already up or successfully brought up, False on failure.
    """
    ns = f"{HERMES_NS_PREFIX}{profile}"
    if profile not in PROFILES:
        return True  # Not a profile we manage infra for — not an error

    if _namespace_exists(ns):
        # Namespace exists — check if deployments are running
        code, stdout, _ = _kubectl(
            ["get", "deploy", "-n", ns, "-o", "jsonpath={.items[*].metadata.name}"],
            check=False,
        )
        if code == 0 and stdout.strip():
            return True  # Already has deployments

    # Bring up infra
    print(f"[infra:auto] {ns} infra missing — bringing up...", file=sys.stderr)
    result = infra_up(profile)
    return result == 0


def idle_teardown(idle_hours: int = 4) -> int:
    """Scale down non-critical pods in namespaces with no recent activity.

    Non-critical = gitnexus deployments (Prefect, litellm, smoke-nginx are critical).
    Only affects hermes-* namespaces not managed by Flux (to avoid Flux reverting).

    Returns count of namespaces scaled down.
    """
    import time

    scaled = 0
    threshold = idle_hours * 3600

    for profile, cfg in PROFILES.items():
        ns = f"{HERMES_NS_PREFIX}{profile}"

        # Skip Flux-managed namespaces (Flux would just revert)
        if _is_flux_managed(ns):
            continue

        if not _namespace_exists(ns):
            continue

        # Check for activity: any running pod with recent start time
        code, stdout, _ = _kubectl(
            ["get", "pods", "-n", ns, "-o",
             "jsonpath={range .items[*]}{.metadata.name}={.status.startTime} {end}"],
            check=False,
        )
        if code != 0:
            continue

        has_recent = False
        now = time.time()
        for entry in stdout.split():
            if "=" not in entry:
                continue
            name, start_time = entry.split("=", 1)
            if not start_time:
                continue
            try:
                # Parse ISO timestamp
                import datetime
                dt = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                age = now - dt.timestamp()
                if age < threshold:
                    has_recent = True
                    break
            except (ValueError, TypeError):
                continue

        if has_recent:
            continue  # Active namespace — skip

        # Scale down non-critical deployments (gitnexus only)
        code, stdout, _ = _kubectl(
            ["get", "deploy", "-n", ns, "-o", "jsonpath={.items[?(@.metadata.name==\"gitnexus\")].metadata.name}"],
            check=False,
        )
        if code == 0 and stdout.strip():
            print(f"[infra:idle] Scaling gitnexus in {ns} to 0 (idle >{idle_hours}h)...")
            _kubectl(["scale", "deployment", "gitnexus", "-n", ns, "--replicas=0"], check=False)
            scaled += 1

    if scaled:
        print(f"[infra:idle] Scaled down {scaled} idle namespace(s).")
    return scaled
