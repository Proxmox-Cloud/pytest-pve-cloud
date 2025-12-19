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
import argparse
import semver
import platform


class TfCodeChangedHandler(FileSystemEventHandler):
    run_lock = threading.Lock()

    def __init__(self, config, workdir = Path.cwd(), wait_seconds = 10):
        self.config = config
        self.workdir = workdir
        self.lock = threading.Lock()
        self.wait_seconds = wait_seconds
        self.timer = None
        self.r = redis.Redis(host='localhost', port=6379, db=0)

        # determine arch tf format
        arch = platform.machine()

        if arch == "x86_64":
            self.arch = "amd64"
        elif arch == "aarch64":
            self.arch = "arm64"
        else:
            raise RuntimeError(f"unsupported arch for tdd {arch}")

        self.run() # build once


    def config_replace(self, value, version):
        if "$ARCH" in value:
            value = value.replace("$ARCH", self.arch)

        if value.startswith("~"):
            value = os.path.expanduser(value)
        
        return value.replace("$VERSION", version)


    def trigger(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.wait_seconds, self.run)
            self.timer.start()


    def run(self):
        with TfCodeChangedHandler.run_lock:
            print("starting build porcess")
            # get the latest git tag version and put timestamp as patch
            # proxmox cloud modules have to be on the same pxc provider version as the one you checked out
            command = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], check=True, capture_output=True, text=True)
            latest_semver = semver.VersionInfo.parse(command.stdout.strip())

            version = str(latest_semver.replace(patch=datetime.now().strftime("%m%d%H%S%f")))

            try:
                for build_command in self.config["build-tf"]["build_commands"]:
                    print(build_command)
                    print([self.config_replace(cmd, version) for cmd in build_command])
                    subprocess.run([self.config_replace(cmd, version) for cmd in build_command], check=True, cwd=self.workdir)

                # publish to local redis
                self.r.set(self.config["redis"]["version_key"], version)
                # no publish message logic for tf providers => end directly in e2e tests

            except subprocess.CalledProcessError as e:
                print(f"Error during build/upload: {e}")

            print("local build successful!")


    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        
        if event.event_type in ["created", "modified", "deleted", "moved"]:
            print(event)
            self.trigger()

    

class PyCodeChangedHandler(FileSystemEventHandler):

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
        with PyCodeChangedHandler.run_lock:
            print("starting build porcess")

            # write custom timestamped version
            version = f"0.0.{datetime.now().strftime("%m%d%H%S%f")}"

            with open(self.workdir / self.config["build-py"]["version_py_path"], "w") as f:
                f.write(f'__version__ = "{version}"\n')

            try:
                for build_command in self.config["build-py"]["build_commands"]:
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


def launch_dog(dog_settings, subdir_name):
    if "build-py" in dog_settings:
        event_handler = PyCodeChangedHandler(dog_settings, get_ipv4(os.getenv("TDDOG_LOCAL_IFACE")), Path(subdir_name))
        observer = Observer()
        observer.schedule(event_handler, f"{subdir_name}/src", recursive=True)
        observer.start()
        return observer
    elif "build-tf" in dog_settings:
        event_handler = TfCodeChangedHandler(dog_settings, Path(subdir_name))
        observer = Observer()
        print("watching on", f"{subdir_name}/internal")
        observer.schedule(event_handler, f"{subdir_name}/internal", recursive=True)
        observer.start()
        return observer
    else:
        raise RuntimeError(f"No known build section in dog settings {subdir_name}!")



def launch(args):
    if not os.getenv("TDDOG_LOCAL_IFACE"):
        print("TDDOG_LOCAL_IFACE not defined!")
        return

    # start docker container for tdd
    subprocess.run(["docker", "start", "pxc-local-registry", "pxc-local-pypi", "pxc-local-redis"], check=True)

    # launch tddogs
    if args.recursive:
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
            if "sub_rebuild_keys" in dog_settings["redis"]:
                for rebuild_key in dog_settings["redis"]["sub_rebuild_keys"]:
                    launch_observers(*toml_file_graph[rebuild_key]) # recurse

            if subdir_name in launched_subdirs:
                return # dont launch twice

            print(f"launching {subdir_name}")

            observer = launch_dog(dog_settings, subdir_name)

            launched_subdirs.add(subdir_name)

            observers.append(observer)


        for subdir_name, dog_settings in toml_file_graph.values():
            launch_observers(subdir_name, dog_settings)

        # let them run indefintely
        try:
            while True:
                time.sleep(1)
        finally:
            for observer in observers:
                observer.stop()

            for observer in observers:
                observer.join()
    else:
        if not os.path.exists("tddog.toml"):
            print("tddog.toml doesnt exist / not in current dir for this project.")
            return
        
        with open("tddog.toml", "rb") as f:
            dog_settings = tomllib.load(f)

        pprint.pprint(dog_settings)
        observer = launch_dog(dog_settings, ".")

        try:
            while True:
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()


# special variables for tddog.toml
# $VERSION => will be replaced with version timestamp
# $REGISTRY_IP => will be replaced with first cli parameter which should point to the local ip address of your dev machine
def main():
    parser = argparse.ArgumentParser(description="Launch watchdog tdd process for e2e development of proxmox cloud.")

    parser = argparse.ArgumentParser()
    parser.add_argument("--recursive", action="store_true", help="Scans recursively for tddog.toml files and launches watchdogs after building dependency graph.")
    parser.set_defaults(func=launch)
    args = parser.parse_args()
    args.func(args)