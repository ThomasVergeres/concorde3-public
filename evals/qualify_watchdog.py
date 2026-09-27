"""Offline real-container check of the operator-owned terminal watchdog."""
import argparse
import json
import subprocess
import time
import uuid
from .lab import command, container_running, stop_container

def main():
    p=argparse.ArgumentParser();p.add_argument("image");a=p.parse_args()
    name="c3-lab-watchdog-"+uuid.uuid4().hex[:12]
    unit=name+"-stop"
    created=False
    try:
        command(["docker","run","-d","--name",name,"--network","none","--memory","64m","--cpus",".2","--pids-limit","32","--cap-drop","ALL","--security-opt","no-new-privileges","--entrypoint","sleep",a.image,"90"])
        created=True
        assert container_running(name)
        # Launcher exits immediately: no live Python process is responsible for
        # sending the future termination signal. The host system manager is.
        command(["sudo","-n","systemd-run","--quiet","--unit",unit,"--on-active","3s","--timer-property=AccuracySec=1s","docker","stop","-t","1",name])
        started=time.monotonic()
        while container_running(name) and time.monotonic()-started<20:time.sleep(.25)
        assert not container_running(name),"independent watchdog did not terminate the process"
        print(json.dumps({"watchdog_qualified":True,"seconds":round(time.monotonic()-started,2),"model_calls":0}))
    finally:
        if created:
            stop_container(name)
            subprocess.run(["docker","rm",name],capture_output=True,timeout=20)
        subprocess.run(["sudo","-n","systemctl","stop",unit+".timer"],capture_output=True,timeout=15)

if __name__=="__main__":main()
