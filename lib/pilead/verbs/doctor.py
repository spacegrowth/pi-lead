"""pilead doctor — check that this machine and state dir are fit to run pi-lead (read-only)."""
import json
import sys

from .. import doctor, state

ORDER = 99
RESOLVE = ()

_STATUS_ORDER = (doctor.PASS, doctor.WARN, doctor.FAIL, doctor.NOT_CHECKED, doctor.SKIP)


INTERRUPTED_EXIT = 130  # 128 + SIGINT: what a shell reports for a program stopped by Ctrl-C


def cmd_doctor(a):
    """Ctrl-C at any point cleans up as a normal run does (a running pi probe is stopped, the
    throwaway directory removed), prints one line on stderr and exits 130 — no traceback."""
    ctx = None
    try:
        sys.stdout.reconfigure(errors="replace")  # a character outside stdout's encoding is replaced, not fatal
        home = state.home_dir(a.home)
        ctx = doctor.build_context(home, offline=a.offline)
        ctx.quick = getattr(a, "quick", False) is True
        return _report(a, doctor.run_checks(ctx))
    except KeyboardInterrupt:
        if ctx is not None:
            ctx.cleanup()  # idempotent: run_checks' own finally has normally done it already
        print("pilead: doctor interrupted", file=sys.stderr)
        return INTERRUPTED_EXIT


def _report(a, results):
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            line = f"{r['status'].ljust(11)}  {r['check']}"
            if r["detail"]:
                line += f"  — {r['detail']}"
            print(line)
            for info in r["info"]:
                print(f"      {info}")
        counts = {s: sum(1 for r in results if r["status"] == s) for s in _STATUS_ORDER}
        print()
        print(f"pilead doctor: {counts[doctor.PASS]} passed, {counts[doctor.WARN]} warnings, "
              f"{counts[doctor.FAIL]} failed, {counts[doctor.NOT_CHECKED]} not checked, "
              f"{counts[doctor.SKIP]} skipped")
    any_fail = any(r["status"] == doctor.FAIL for r in results)
    any_warn = any(r["status"] == doctor.WARN for r in results)
    any_not_checked = any(r["status"] == doctor.NOT_CHECKED for r in results)
    if any_fail or (a.strict and any_warn):
        return 1
    if any_not_checked:
        return 3
    return 0


def register(verb):
    p = verb("doctor", cmd_doctor, "check that this machine and state dir are fit to run pi-lead (read-only)")
    p.add_argument("--offline", action="store_true", help="skip the checks that start pi")
    p.add_argument("--quick", action="store_true",
                   help="skip the slower checks (the ones that wait for pi's list of commands)")
    p.add_argument("--strict", action="store_true", help="a WARN also counts as a FAIL for the exit code")
    p.add_argument("--json", action="store_true")
