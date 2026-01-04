import argparse
import os
import platform
import pprint
import re
import subprocess
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path

import netifaces
import redis
import semver
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


# parses through git tags, sorts out semvers and returns the latest
def get_latest_semver_tag(workdir):
    result = subprocess.run(["git", "tag"], capture_output=True, text=True, cwd=workdir)

    if result.returncode != 0:
        raise Exception(f"Error getting Git tags: {result.stderr}")

    tags = result.stdout.splitlines()

    semver_pattern = re.compile(r"^(v?\d+\.\d+\.\d+)$")

    semver_tags = [tag.lstrip("v") for tag in tags if semver_pattern.match(tag)]

    if not semver_tags:
        raise Exception("No semver tags found!")

    semver_tags.sort(key=semver.VersionInfo.parse, reverse=True)

    return semver.VersionInfo.parse(semver_tags[0])


def get_ipv4(iface):
    if iface in netifaces.interfaces():
        info = netifaces.ifaddresses(iface)
        ipv4 = info.get(netifaces.AF_INET, [{}])[0].get("addr")
        return ipv4
    return None


# passed to all observers, used to print / collect error messages from layered builds
class DoneHandler:

    def __init__(self):
        self.active_runs = 0
        self.current_errors = []
        print("init done handler")

    def event_triggerd(self):
        self.active_runs += 1

    def add_error(self, handler_path, error):
        self.current_errors.append(f"{handler_path} - err: {error}")

    def run_finished(self):
        self.active_runs -= 1


# code watcher for terraform providers
# watches only on the internal/ folder
class TfCodeChangedHandler(FileSystemEventHandler):
    run_lock = threading.Lock()

    def __init__(self, config, done_handler, workdir=Path.cwd(), wait_seconds=10):
        self.config = config
        self.done_handler = done_handler
        self.workdir = workdir
        self.lock = threading.Lock()
        self.wait_seconds = wait_seconds
        self.timer = None
        self.r = redis.Redis(host="localhost", port=6379, db=0)

        # determine arch tf format
        arch = platform.machine()

        if arch == "x86_64":
            self.arch = "amd64"
        elif arch == "aarch64":
            self.arch = "arm64"
        else:
            raise RuntimeError(f"unsupported arch for tdd {arch}")

    def config_replace(self, value, version):
        if "$ARCH" in value:
            value = value.replace("$ARCH", self.arch)

        if value.startswith("~"):
            value = os.path.expanduser(value)

        if "$REGISTRY_IP" in value:
            value = value.replace("$REGISTRY_IP", self.local_ip)

        return value.replace("$VERSION", version)

    def trigger(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.wait_seconds, self.run)
            self.timer.start()

    def run(self):
        self.done_handler.event_triggerd()
        with TfCodeChangedHandler.run_lock:
            print("starting build porcess")
            # get the latest git tag version and put timestamp as patch
            # proxmox cloud modules have to be on the same pxc provider version as the one you checked out
            latest_semver_tag = get_latest_semver_tag(self.workdir)
            version = str(
                latest_semver_tag.replace(patch=datetime.now().strftime("%m%d%H%S%f"))
            )

            error = False
            try:
                for build_command in self.config["build-tf"]["build_commands"]:
                    print(build_command)
                    print([self.config_replace(cmd, version) for cmd in build_command])
                    subprocess.run(
                        [self.config_replace(cmd, version) for cmd in build_command],
                        check=True,
                        cwd=self.workdir,
                    )

                # publish to local redis
                self.r.set(self.config["redis"]["version_key"], version)
                # no publish message logic for tf providers => end directly in e2e tests

            except subprocess.CalledProcessError as e:
                print(f"Error during build/upload: {e}")
                self.done_handler.add_error(self.workdir, e)
                error = True

            if not error:
                print("local build successful!")
            else:
                print("build errors!")

            self.done_handler.run_finished()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        if event.event_type in ["created", "modified", "deleted", "moved"]:
            print(event)
            self.trigger()


# code watcher for pypi projects, watches the src/ folder
# also supports rebuilds via redis pub sub mechanisms
class PyCodeChangedHandler(FileSystemEventHandler):

    run_lock = threading.Lock()

    def __init__(
        self, config, done_handler, local_ip, workdir=Path.cwd(), wait_seconds=10
    ):
        self.config = config
        self.done_handler = done_handler
        self.local_ip = local_ip
        self.workdir = workdir
        self.lock = threading.Lock()
        self.wait_seconds = wait_seconds
        self.timer = None
        self.r = redis.Redis(host="localhost", port=6379, db=0)

        threading.Thread(
            target=self.dependency_listener, daemon=True
        ).start()  # daemon means insta exit

    def dependency_listener(self):
        pubsub = self.r.pubsub()
        if "sub_rebuild_keys" in self.config["build"]:
            for rebuild_key in self.config["build"]["sub_rebuild_keys"]:
                pubsub.subscribe(rebuild_key)

        for message in pubsub.listen():
            if message["type"] == "message":
                print(
                    f"new {message['channel'].decode()} version build",
                    message["data"].decode(),
                )
                self.run()  # rerun build process

    def config_replace(self, value, version):
        if "$REGISTRY_IP" in value:
            value = value.replace("$REGISTRY_IP", self.local_ip)

        if value.startswith("~"):
            value = os.path.expanduser(value)

        return value.replace("$VERSION", version)

    def trigger(self):

        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.wait_seconds, self.run)
            self.timer.start()

    def run(self, initial=False):
        self.done_handler.event_triggerd()
        with PyCodeChangedHandler.run_lock:
            print("starting build porcess")

            # write custom timestamped version
            latest_semver_tag = get_latest_semver_tag(self.workdir)
            version = str(
                latest_semver_tag.replace(patch=datetime.now().strftime("%m%d%H%S%f"))
            )

            with open(
                self.workdir / self.config["build"]["dyn_version_py_path"], "w"
            ) as f:
                f.write(f'__version__ = "{version}"\n')

            error = False
            try:
                for build_command in self.config["build"]["build_commands"]:
                    print(build_command)
                    print([self.config_replace(cmd, version) for cmd in build_command])
                    subprocess.run(
                        [self.config_replace(cmd, version) for cmd in build_command],
                        check=True,
                        cwd=self.workdir,
                    )

                # publish to local redis
                self.r.set(self.config["redis"]["version_key"], version)
                self.r.publish(
                    self.config["redis"]["version_key"], version
                )  # for any other build watchdogs listing

            except subprocess.CalledProcessError as e:
                print(f"Error during build/upload: {e}")
                self.done_handler.add_error(self.workdir, e)
                error = True

            if not error:
                print("local build successful!")
            else:
                print("build errors!")

            self.done_handler.run_finished()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if (
            event.is_directory
            or ".egg-info" in event.src_path
            or "__pycache__" in event.src_path
            or "_version.py" in event.src_path
        ):
            return

        if event.event_type in ["created", "modified", "deleted", "moved"]:
            print(event)
            self.trigger()


# based on the toml keys we launch our watchdog listeners
def launch_dog(dog_settings, done_handler, subdir_name):
    observers = []
    handlers = []
    if "build" in dog_settings:
        event_handler = PyCodeChangedHandler(
            dog_settings,
            done_handler,
            get_ipv4(os.getenv("TDDOG_LOCAL_IFACE")),
            Path(subdir_name),
        )
        handlers.append(event_handler)
        observer = Observer()
        observer.schedule(event_handler, f"{subdir_name}/src", recursive=True)
        observer.start()
        observers.append(observer)

    if "build-tf" in dog_settings:
        event_handler = TfCodeChangedHandler(
            dog_settings, done_handler, Path(subdir_name)
        )
        handlers.append(event_handler)
        observer = Observer()
        observer.schedule(event_handler, f"{subdir_name}/internal", recursive=True)
        observer.start()
        observers.append(observer)

    return observers, handlers


# the [local] block will cause a one time init when launching tddog
# this is useful for running stuff like pip install -e . automatically
# aswell as writing dynamic _version.py files
def init_local(dog_settings, subdir_name):
    # write custom timestamped version
    latest_semver_tag = get_latest_semver_tag(Path(subdir_name))
    version = str(
        latest_semver_tag.replace(patch=datetime.now().strftime("%m%d%H%S%f"))
    )

    with open(
        Path(subdir_name) / dog_settings["local"]["dyn_version_py_path"], "w"
    ) as f:
        f.write(f'__version__ = "{version}"\n')

    # just install locally with all deps unmod
    # install the package
    subprocess.run(
        [
            "pip",
            "install",
            "--upgrade",
            "--index-url",
            "http://localhost:8088/simple",
            "--trusted-host",
            "localhost",
            "-e",
            ".",
        ],
        check=True,
        cwd=Path(subdir_name),
    )


# launching tddog in recursive mode causes it to check for tddog.toml files in
# all subfolders, build a dependency graph based on the [redis] version_key
# and dependant projects in [build] sub_rebuild_keys / [init] dep_build_keys.
# it will launch those furthest up the chain first
def dog_recursive(done_handler):

    # find all tddog toml files
    toml_file_graph = {}

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

    # do recursive launching, to launch core artifact builds first
    launched_subdirs = set()

    # container for the recursively launched observer threads
    observers = []
    handlers = []  # need these for launching initial builds

    def launch_observers_recursive(subdir_name, dog_settings):
        # recurse down to artifacts that dont have any dependencies

        # check if artifact has python build dependencies
        if "build" in dog_settings and "sub_rebuild_keys" in dog_settings["build"]:
            for rebuild_key in dog_settings["build"]["sub_rebuild_keys"]:
                launch_observers_recursive(*toml_file_graph[rebuild_key])

        # check if artifact has any dependencies in the local init section - also launch the dependencies first
        if "local" in dog_settings and "dep_build_keys" in dog_settings["local"]:
            for dep_build_key in dog_settings["local"]["dep_build_keys"]:
                launch_observers_recursive(*toml_file_graph[dep_build_key])

        # we dont want to launch observers twice
        if subdir_name in launched_subdirs:
            return

        # we recursed down to an artifact without any dependencies / processed the dependencies first

        print(f"launching {subdir_name}")
        dog_observers, dog_handlers = launch_dog(
            dog_settings, done_handler, subdir_name
        )
        observers.extend(
            dog_observers
        )  # launch the build observer (that also builds initially)

        handlers.extend(dog_handlers)  # add handler for initial build

        # install the project locally if required
        if "local" in dog_settings:
            init_local(dog_settings, subdir_name)

        # add project to launch guard
        launched_subdirs.add(subdir_name)

    # invoke our recursive launch function
    for subdir_name, dog_settings in toml_file_graph.values():
        launch_observers_recursive(subdir_name, dog_settings)

    # trigger initial builds
    for handler in handlers:
        handler.trigger()

    # let them run indefintely
    try:
        done_reached = False
        while True:
            if done_handler.active_runs == 0 and not done_reached:
                print("all builds finished")
                done_reached = True

                if done_handler.current_errors:
                    print("found errors in builds")
                    for error in done_handler.current_errors:
                        print(error)

                    # clean errors
                    done_handler.current_errors = []
                else:
                    print(
                        "no errors in any builds! good to go - run can run e2e tests now!"
                    )

            if done_handler.active_runs > 0:
                done_reached = False

            time.sleep(1)
    finally:
        for observer in observers:
            observer.stop()

        for observer in observers:
            observer.join()


def launch(args):
    if not os.getenv("TDDOG_LOCAL_IFACE"):
        print("TDDOG_LOCAL_IFACE not defined!")
        return

    # start docker container for tdd => this assumes these containers as described in the tdd documentation
    subprocess.run(
        ["docker", "start", "pxc-local-registry", "pxc-local-pypi", "pxc-local-redis"],
        check=True,
    )

    done_handler = DoneHandler()

    if args.recursive:
        dog_recursive(done_handler)
    else:
        # standalone launching is simpler, no need for any recursion
        if not os.path.exists("tddog.toml"):
            print("tddog.toml doesnt exist / not in current dir for this project.")
            return

        with open("tddog.toml", "rb") as f:
            dog_settings = tomllib.load(f)

        pprint.pprint(dog_settings)
        observers, handlers = launch_dog(dog_settings, done_handler, ".")

        for handler in handlers:
            handler.trigger()  # initial build

        if "local" in dog_settings:
            init_local(dog_settings, ".")

        try:
            # no need for final build logic here
            while True:
                time.sleep(1)
        finally:
            for observer in observers:
                observer.stop()

            for observer in observers:
                observer.join()


def main():
    parser = argparse.ArgumentParser(
        description="Launch watchdog tdd process for e2e development of proxmox cloud."
    )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scans recursively for tddog.toml files and launches watchdogs after building dependency graph.",
    )
    parser.set_defaults(func=launch)
    args = parser.parse_args()
    args.func(args)
