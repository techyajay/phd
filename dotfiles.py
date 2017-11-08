#!/usr/bin/env python2.7
from __future__ import print_function

import json
import logging
import os
import socket
import sys

from argparse import ArgumentParser
from collections import deque
from time import time

from util import *


DOTFILES = os.path.expanduser("~/.dotfiles")
PRIVATE = os.path.expanduser("~/Dropbox/Shared")


class InvalidTaskError(Exception): pass


# must be imported before get_tasks()
from tasks import *


def get_tasks():
    """ generate the list of tasks to run, respecting dependencies """
    tasks = [x[1]() for x in inspect.getmembers(sys.modules[__name__], is_runnable_task)]
    tasks = sorted(tasks, key=lambda task: type(task).__name__)

    queue = deque(tasks)
    ordered = list()
    while len(queue):
        task = queue.popleft()
        deps = getattr(task, "__deps__", [])
        deps += getattr(task, "__" + PLATFORM + "_deps__", [])

        for dep in deps:
            if dep not in set(type(task) for task in ordered):
                queue.append(task)
                break
        else:
            ordered.append(task)

    return ordered


def main(*args):
    """ main dotfiles method """

    # Parse arguments
    parser = ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--verbose', action='store_true')
    group.add_argument('--debug', action='store_true')
    args = parser.parse_args(args)

    # Configure logger
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.WARNING)
    logging.basicConfig(format="%(message)s")

    # Get the list of tasks to run
    logging.debug("creating tasks list ...")
    queue = get_tasks()
    done = set()
    ntasks = len(queue)
    print("Running " + Colors.BOLD + str(ntasks) + Colors.END +
          " tasks on", PLATFORM + ":")

    # Run the tasks
    errored = False
    try:
        for i, task in enumerate(queue):
            # Task setup
            logging.debug(type(task).__name__ + " setup")

            # Resolve and run setup() method:
            get_task_method(task, "setup")()

            done.add(task)

            # Resolve and run run() method:
            print("  [{:2d}/{:2d}]".format(i + 1, ntasks) + Colors.BOLD,
                  type(task).__name__, Colors.END + "...", end=" ")
            if logging.getLogger().level <= logging.INFO:
                print()
            sys.stdout.flush()
            start_time = time()
            get_task_method(task, "run")()
            runtime = time() - start_time

            print("{:.3f}s".format(runtime))
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\ninterrupt")
        errored = True
    except Exception as e:
        print(Colors.BOLD + Colors.RED + type(e).__name__)
        print(e, Colors.END)
        errored = True
        if logging.getLogger().level <= logging.DEBUG:
            raise
    finally:
        # Task teardowm
        logging.debug("")
        for task in done:
            logging.debug("  " + Colors.BOLD + type(task).__name__ + " teardown" + Colors.END)
            get_task_method(task, "teardown")()

            # remove any temporary files
            for file in getattr(task, "__tmpfiles__", []):
                file = os.path.abspath(os.path.expanduser(file))
                if os.path.exists(file):
                    logging.debug("rm {file}".format(**vars()))
                    os.remove(file)

    if not errored:
        print("done")
    return 1 if errored else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
