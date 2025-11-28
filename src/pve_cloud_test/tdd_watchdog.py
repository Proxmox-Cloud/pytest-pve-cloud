import time
import redis
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
import threading
import subprocess
from datetime import datetime
import os
import tomllib
import pprint
import netifaces
import sys
from pathlib import Path


class CodeChangedHandler(FileSystemEventHandler):

    run_lock = threading.Lock()

    def __init__(self, config, local_ip, workdir = Path.cwd(), wait_seconds = 10):
        self.config = config
        self.local_ip = local_ip
        self.workdir = workdir
        self.lock = threading.Lock()
        self.wait_seconds = wait_seconds
        self.timer = None
        self.r = redis.Redis(host='localhost', port=6379, db=0)
        self.run() # build once
        threading.Thread(target=self.dependency_listener, daemon=True).start() # daemon means insta exit
        

    def dependency_listener(self):
        pubsub = self.r.pubsub()
        for rebuild_key in self.config["redis"]["sub_rebuild_keys"]:
            pubsub.subscribe(rebuild_key)

        for message in pubsub.listen():
            if message['type'] == 'message':
                print(f"new {message['channel'].decode()} version build", message['data'].decode())
                self.run() # rerun build process


    def config_replace(self, value, version):
        if "$REGISTRY_IP" in value:
            value = value.replace("$REGISTRY_IP", self.local_ip)
        
        # dynamic redis replacement
        for env_key, redis_key in self.config["redis"]["env_key_mapping"].items():
            value = value.replace(f"${env_key}", self.r.get(redis_key).decode())

        return value.replace("$VERSION", version)


    def trigger(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.wait_seconds, self.run)
            self.timer.start()


    def run(self):
        with CodeChangedHandler.run_lock:
            print("starting build porcess")

            # write custom timestamped version
            version = f"0.0.{datetime.now().strftime("%m%d%H%S%f")}"

            with open(self.workdir / self.config["build"]["version_py_path"], "w") as f:
                f.write(f'__version__ = "{version}"\n')

            try:
                for build_command in self.config["build"]["build_commands"]:
                    print(build_command)
                    print([self.config_replace(cmd, version) for cmd in build_command])
                    subprocess.run([self.config_replace(cmd, version) for cmd in build_command], check=True, cwd=self.workdir)

                # publish to local redis
                self.r.set(self.config["redis"]["version_key"], version)
                self.r.publish(self.config["redis"]["version_key"], version) # for any other build watchdogs listing

            except subprocess.CalledProcessError as e:
                print(f"Error during build/upload: {e}")

            print("local build successful!")


    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or ".egg-info" in event.src_path or "__pycache__" in event.src_path or "_version.py" in event.src_path:
            return
        
        if event.event_type in ["created", "modified", "deleted", "moved"]:
            print(event)
            self.trigger()


def get_ipv4(iface):
    if iface in netifaces.interfaces():
        info = netifaces.ifaddresses(iface)
        ipv4 = info.get(netifaces.AF_INET, [{}])[0].get("addr")
        return ipv4
    return None

# special variables for tddog.toml
# $VERSION => will be replaced with version timestamp
# $REGISTRY_IP => will be replaced with first cli parameter which should point to the local ip address of your dev machine
def main():
    if not os.getenv("TDDOG_LOCAL_IFACE"):
        print("TDDOG_LOCAL_IFACE not defined!")
        return

    # start docker container for tdd
    subprocess.run(["docker", "start", "pxc-local-registry", "pxc-local-pypi", "pxc-local-redis"], check=True)

    if len(sys.argv) == 2 and sys.argv[1] == "--recursive":
        toml_file_graph = {}

        # build graph dir to launch dependant tddogs first
        for subdir in Path.cwd().iterdir():
            if subdir.is_dir():
                tddog_file = subdir / "tddog.toml"
                if tddog_file.exists():
                    with tddog_file.open("rb") as f:
                        dog_settings = tomllib.load(f)
                        version_key = dog_settings["redis"]["version_key"]

                        toml_file_graph[version_key] = (subdir.name, dog_settings)
        
        if not toml_file_graph:
            print("no tddog.toml files found!")
            return

        # prevent launching multiple observers
        launched_subdirs = set()
        observers = []

        def launch_observers(subdir_name, dog_settings):
            # launch sub rebuilds first
            for rebuild_key in dog_settings["redis"]["sub_rebuild_keys"]:
                launch_observers(*toml_file_graph[rebuild_key]) # recurse

            if subdir_name in launched_subdirs:
                return # dont launch twice

            print(f"launching {subdir_name}")
            event_handler = CodeChangedHandler(dog_settings, get_ipv4(os.getenv("TDDOG_LOCAL_IFACE")), Path(subdir_name))
            observer = Observer()
            observer.schedule(event_handler, f"{subdir_name}/src", recursive=True)
            observer.start()

            launched_subdirs.add(subdir_name)

            observers.append(observer)

        for subdir_name, dog_settings in toml_file_graph.values():
            launch_observers(subdir_name, dog_settings)

        # let them run
        try:
            while True:
                time.sleep(1)
        finally:
            for observer in observers:
                observer.stop()

            for observer in observers:
                observer.join()

    else:
        # single project launch
        if not os.path.exists("tddog.toml"):
            print("tddog.toml doesnt exist / not in current dir for this project.")
            return
        
        with open("tddog.toml", "rb") as f:
            dog_settings = tomllib.load(f)

        pprint.pprint(dog_settings)
        print(dog_settings["redis"]["env_key_mapping"])

        event_handler = CodeChangedHandler(dog_settings, get_ipv4(os.getenv("TDDOG_LOCAL_IFACE")))
        observer = Observer()
        observer.schedule(event_handler, "src", recursive=True)
        observer.start()
        try:
            while True:
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()