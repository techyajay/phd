#
# libcecl utilities.
#
# WARNING: This code is custom tailored to one specific experimental
# setup and methodology. It is EXTREMELY fragile code!
#
from __future__ import division,absolute_import,print_function,unicode_literals

import os
import re
import six
import sys

import editdistance

import labm8
from labm8 import fs

import smith
from smith import clutil


class NameInferenceException(smith.SmithException): pass

def parse_cecl_log(log):
    """
    Interpret and parse the output of a libcecl instrument kernel.
    """
    lines = []

    insrc = False
    srcbuf = ''
    with open(log) as infile:
        contents = infile.read()

    for line in contents.split('\n'):
        if line.strip() == 'BEGIN PROGRAM SOURCE':
            insrc = True
        elif line.strip() == 'END PROGRAM SOURCE':
            insrc = False
            kernels = [clutil.strip_attributes(x)
                       for x in clutil.get_cl_kernels(srcbuf)]
            names = [x.split()[2].split('(')[0] for x in kernels]
            lines[-1].append(dict(zip(names, kernels)))
            srcbuf = ''
        elif insrc:
            srcbuf += line + "\n"
        else:
            components = [x.strip() for x in line.split(';')]
            if components[0]:
                lines.append(components)
    return lines


def get_kernels(parsed):
    compiled_k = {}     # maps function names to implementations
    enqueued_k = set()  # store all functions which actually get executed

    for line in parsed:
        if line[0] == 'clCreateProgramWithSource':
            for function_name,source in six.iteritems(line[1]):
                compiled_k[function_name] = source
        elif line[0] == 'clEnqueueNDRangeKernel':
            function_name = line[1]
            enqueued_k.add(function_name)
        elif line[0] == 'clEnqueueTask':
            function_name = line[1]
            print("TASK", function_name)
            enqueued_k.add(function_name)

    # Check that we have source code for all enqueued kernels.
    undefined = []
    for kernel in enqueued_k:
        if kernel not in compiled_k:
            undefined.append(kernel)
    if len(undefined):
        print("undefined kernels:",
              ", ".join("'{}'".format(x) for x in undefined), file=sys.stderr)

    # Remove any kernels which are not used in the source code.
    # unused = []
    for key in list(compiled_k.keys()):
        if key not in enqueued_k:
            # unused.append(key)
            compiled_k.pop(key)
    # if len(unused):
    #     print("unused kernels:", ', '.join("'{}'".format(x) for x in unused))

    return compiled_k


def get_transfers(parsed):
    transfers = {}  # maps buffer names to (size,elapsed) tuples

    # TODO:
    for line in parsed:
        if line[0] == 'clEnqueueReadBuffer':
            pass


def path_to_benchmark_and_dataset(path):
    basename = fs.basename(path)
    if basename.startswith("npb-"):
        return (
            re.sub(r"(npb-3\.3-[A-Z]+)\.[A-Z]+\.[cg]pu\.out", r"\1", basename),
            re.sub(r"npb-3\.3-[A-Z]+\.([A-Z]+)\.[cg]pu\.out", r"\1", basename))
    elif basename.startswith("nvidia-"):
        return (
            re.sub(r"(nvidia-4\.2-)ocl([a-zA-Z]+)", r"\1\2", basename),
            "default")
    elif basename.startswith("parboil-"):
        components = basename.split("-")
        return ("-".join(components[:-1]), components[-1])
    else:
        return basename, "default"


def process_cecl_log(log):
    benchmark, dataset = path_to_benchmark_and_dataset(log)
    # print(benchmark, dataset)
    parsed = parse_cecl_log(log)
    kernels = get_kernels(parsed)

    for kernel in kernels.keys():
        print('-'.join((benchmark, dataset, kernel)))



def log2features(log, out=sys.stdout, metaout=sys.stderr):
    process_cecl_log(log)


def dir2features(logdir, out=sys.stdout, metaout=sys.stderr):
    """
    Directory structure:

      <logdir>
      <logdir>/<iteration>
      <logdir>/<iteration>/<device>
      <logdir>/<iteration>/<device>/<log>
    """
    runs = fs.ls(logdir, abspaths=True)
    devices = fs.ls(runs[0])
    logs = [x for x in fs.ls(logdir, abspaths=True, recursive=True)
             if fs.isfile(x)]

    print("summarising", len(logs), "logs:", file=metaout)
    print("   # runs:", len(runs), file=metaout)
    print("   # devices:", len(devices), file=metaout)

    for path in logs:
        process_cecl_log(path)
