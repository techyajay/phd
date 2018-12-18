"""Reachability analysis datasets."""
import multiprocessing
import pathlib
import typing

import pandas as pd
from absl import app
from absl import flags

from compilers.llvm import clang
from datasets.linux import linux
from datasets.opencl.device_mapping import \
  opencl_device_mapping_dataset as ocl_dataset
from deeplearning.clgen.preprocessors import opencl
from experimental.compilers.reachability import control_flow_graph as cfg
from experimental.compilers.reachability import llvm_util
from labm8 import decorators


FLAGS = flags.FLAGS


def BytecodeFromOpenClString(opencl_string: str) -> str:
  """Create bytecode from OpenCL source string.

  Args:
    opencl_string: A string of OpenCL code.

  Returns:
    The bytecode as a string.

  Raises:
    ClangException: If compiling to bytecode fails.
  """
  # Use -O3 to reduce CFGs.
  clang_args = opencl.GetClangArgs(use_shim=False) + [
    '-O3', '-S', '-emit-llvm', '-o', '-', '-i', '-']
  process = clang.Exec(clang_args, stdin=opencl_string)
  if process.returncode:
    raise clang.ClangException("clang failed with returncode "
                               f"{process.returncode}:\n{process.stderr}")
  return process.stdout


def CreateControlFlowGraphFromOpenClKernel(
    kernel_name: str,
    opencl_kernel: str) -> typing.Optional[cfg.ControlFlowGraph]:
  """Try to create a CFG proto from an opencl kernel.

  Args:
    kernel_name: The name of the OpenCL kernel defined in opencl_kernel.
    opencl_kernel: A string of OpenCL. This should contain a single kernel
      definition.

  Returns:
    A ControlFlowGraph instance, or None if compilation to bytecode fails.

  Raises:
    ClangException: If compiling to bytecode fails.
    ValueError: If opencl_kernel contains multiple functions.
  """
  bytecode = BytecodeFromOpenClString(opencl_kernel)

  # Extract a single dot source from the bytecode.
  dot_generator = llvm_util.DotCfgsFromBytecode(bytecode)
  dot = next(dot_generator)
  try:
    next(dot_generator)
    raise ValueError("Bytecode produced more than one dot source!")
  except StopIteration:
    pass

  # Instantiate a CFG from the dot source.
  graph = llvm_util.ControlFlowGraphFromDotSource(dot)

  # Set the name of the graph to the kernel name. This is because the src code
  # has been preprocessed, so that each kernel is named 'A'.
  graph.graph['name'] = kernel_name

  return graph


def ProcessProgramDfIterItem(
    row: typing.Dict[str, str]
) -> typing.Optional[typing.Dict[str, typing.Any]]:
  benchmark_suite_name = row['program:benchmark_suite_name']
  benchmark_name = row['program:benchmark_name']
  kernel_name = row['program:opencl_kernel_name']
  src = row['program:opencl_src']

  try:
    graph = CreateControlFlowGraphFromOpenClKernel(
        kernel_name, src).ValidateControlFlowGraph(strict=False)
  except (clang.ClangException, cfg.MalformedControlFlowGraphError):
    return None

  row = CfgDfRowFromControlFlowGraph(graph)
  row.update({
    'program:benchmark_suite_name': benchmark_suite_name,
    'program:benchmark_name': benchmark_name,
    'program:opencl_kernel_name': kernel_name,
  })
  return row


def CfgDfRowFromControlFlowGraph(
    graph: cfg.ControlFlowGraph) -> typing.Dict[str, typing.Any]:
  return {
    'cfg:graph': graph,
    'cfg:block_count': graph.number_of_edges(),
    'cfg:edge_count': graph.number_of_edges(),
    'cfg:edge_density': graph.number_of_edges() / (
        graph.number_of_nodes() * graph.number_of_nodes()),
    'cfg:is_valid': graph.IsValidControlFlowGraph(strict=True),
  }


class OpenClDeviceMappingsDataset(ocl_dataset.OpenClDeviceMappingsDataset):
  """An extension of the OpenCL device mapping dataset for control flow graphs.

  The returned DataFrame has the following schema:

    program:benchmark_suite_name (str): The name of the benchmark suite.
    program:benchmark_name (str): The name of the benchmark program.
    program:opencl_kernel_name (str): The name of the OpenCL kernel.
    cfg:graph (ControlFlowGraph): A control flow graph instance.
    cfg:block_count (int): The number of basic blocks in the CFG.
    cfg:edge_count (int): The number of edges in the CFG.
    cfg:edge_density (float): Number of edges / possible edges, in range [0,1].
    cfg:is_valid (bool): Whether the CFG is valid.
  """

  @decorators.memoized_property
  def cfgs_df(self) -> pd.DataFrame:
    programs_df = self.programs_df.reset_index()

    # Process each row of the table in parallel.
    pool = multiprocessing.Pool()
    rows = []
    for row in pool.imap_unordered(
        ProcessProgramDfIterItem, [d for i, d in programs_df.iterrows()]):
      if row:
        rows.append(row)

    # Create the output table.
    df = pd.DataFrame(rows, columns=[
      'program:benchmark_suite_name',
      'program:benchmark_name',
      'program:opencl_kernel_name',
      'cfg:graph',
      'cfg:block_count',
      'cfg:edge_count',
      'cfg:edge_density',
      'cfg:is_valid',
    ])

    df.set_index([
      'program:benchmark_suite_name',
      'program:benchmark_name',
      'program:opencl_kernel_name',
    ], inplace=True)
    df.sort_index(inplace=True)
    return df


def BytecodeFromLinuxSrc(path: pathlib.Path) -> str:
  """Create bytecode from OpenCL source string.

  Args:
    path: The path of the source file.

  Returns:
    The bytecode as a string.

  Raises:
    ClangException: If compiling to bytecode fails.
  """
  # Use -O3 to reduce CFGs.
  linux_include_dir = linux.LinuxSourcesDataset().src_tree_root / 'include'
  clang_args = [
    '-O3', '-S', '-emit-llvm', '-o', '-', '-I', str(linux_include_dir),
    str(path)]
  process = clang.Exec(clang_args)
  if process.returncode:
    raise clang.ClangException("clang failed with returncode "
                               f"{process.returncode}:\n{process.stderr}")
  return process.stdout


def CreateControlFlowGraphFromLinuxSrc(
    path: pathlib.Path) -> typing.Optional[cfg.ControlFlowGraph]:
  """Try to create a CFG proto from a Linux C source file.

  Args:
    path: The path of the source file.

  Returns:
    A ControlFlowGraph instance, or None on failure.

  Raises:
    ClangException: If compiling to bytecode fails.
  """
  bytecode = BytecodeFromLinuxSrc(path)

  # Extract a single dot source from the bytecode.
  dot_generator = llvm_util.DotCfgsFromBytecode(bytecode)
  graphs = []
  for dot in dot_generator:
    # Instantiate a CFG from the dot source.
    try:
      graphs.append(llvm_util.ControlFlowGraphFromDotSource(dot))
    except cfg.MalformedControlFlowGraphError:
      continue

  return graphs


def ProcessLinuxSrc(
    path: pathlib.Path) -> typing.Optional[typing.Dict[str, typing.Any]]:
  try:
    graphs = CreateControlFlowGraphFromLinuxSrc(
        path).ValidateControlFlowGraph(strict=False)
  except clang.ClangException:
    return None

  rows = []
  for graph in graphs:
    row = CfgDfRowFromControlFlowGraph(graph)
    row.update({
      'program:'
    })
    rows.append(row)

  return rows


class LinuxSourcesDataset(linux.LinuxSourcesDataset):
  """Control flow graphs from a subset of the Linux source tree.

  The returned DataFrame has the following schema:

    program:src_relpath (str): The path of the source file within the linux
      source tree.
    cfg:graph (ControlFlowGraph): A control flow graph instance.
    cfg:block_count (int): The number of basic blocks in the CFG.
    cfg:edge_count (int): The number of edges in the CFG.
    cfg:edge_density (float): Number of edges / possible edges, in range [0,1].
    cfg:is_valid (bool): Whether the CFG is valid.
  """

  @decorators.memoized_property
  def cfgs_df(self) -> pd.DataFrame:
    # Process each row of the table in parallel.
    pool = multiprocessing.Pool()
    rows = []
    for row_batch in pool.imap_unordered(ProcessLinuxSrc, self.kernel_srcs):
      if row_batch:
        rows += row_batch

    # Create the output table.
    df = pd.DataFrame(rows, columns=[
      'program:src_relpath',
      'cfg:graph',
      'cfg:block_count',
      'cfg:edge_count',
      'cfg:edge_density',
      'cfg:is_valid',
    ])

    df.set_index([
      'program:src_relpath',
    ], inplace=True)
    df.sort_index(inplace=True)
    return df


def main(argv):
  """Main entry point."""
  if len(argv) > 1:
    raise app.UsageError("Unknown arguments: '{}'.".format(' '.join(argv[1:])))


if __name__ == '__main__':
  app.run(main)