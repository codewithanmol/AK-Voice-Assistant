"""Install & start the AKUnlock service (needs ADMIN).

Usage (admin):  python install_service.py
Also:          python install_service.py --start|--stop|--uninstall|--status
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from service import AKUnlockService  # noqa: E402
import win32service  # noqa: E402
import win32serviceutil  # noqa: E402

_NAME = AKUnlockService._svc_name_
# pythonservice.exe loader format: [dir\]module.ClassName — dir gets added
# to sys.path, then "service.AKUnlockService" is imported.
_CLS = os.path.join(_HERE, "service.AKUnlockService")


def _exists() -> bool:
    try:
        win32serviceutil.QueryServiceStatus(_NAME)
        return True
    except Exception:
        return False


def install() -> None:
    if _exists():
        try:
            win32serviceutil.StopService(_NAME)
        except Exception:
            pass
        try:
            win32serviceutil.RemoveService(_NAME)
        except Exception:
            pass
    win32serviceutil.InstallService(
        _CLS, _NAME, AKUnlockService._svc_display_name_,
        startType=win32service.SERVICE_AUTO_START,
        description=AKUnlockService._svc_description_)
    print("Installed.")


def main() -> None:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "install").lower()
    if arg == "install":
        install()
        win32serviceutil.StartService(_NAME)
        print("AKUnlock service STARTED (auto-start at boot).")
        print("Dawood: ab 'set unlock password' bolo, phir 'unlock'.")
        print("Log: %s" % os.path.join(_HERE, "service.log"))
    elif arg == "start":
        win32serviceutil.StartService(_NAME)
        print("Started.")
    elif arg == "stop":
        win32serviceutil.StopService(_NAME)
        print("Stopped.")
    elif arg == "uninstall":
        try:
            win32serviceutil.StopService(_NAME)
        except Exception:
            pass
        win32serviceutil.RemoveService(_NAME)
        print("Uninstalled.")
    elif arg == "status":
        status = win32serviceutil.QueryServiceStatus(_NAME)
        print("State:", status[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()