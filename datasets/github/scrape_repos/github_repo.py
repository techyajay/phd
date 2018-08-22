"""This file defines the GitHubRepo class."""
import multiprocessing

import hashlib
import humanize
import pathlib
import progressbar
import subprocess
import typing
from absl import flags
from absl import logging
from phd.lib.labm8 import pbutil

from datasets.github.scrape_repos import contentfiles
from datasets.github.scrape_repos.preprocessors import preprocessors
from datasets.github.scrape_repos.preprocessors import public
from datasets.github.scrape_repos.proto import scrape_repos_pb2


FLAGS = flags.FLAGS


class GitHubRepo(object):
  """Representation of a GitHub repo."""

  def __init__(self, metafile: pathlib.Path):
    self.metafile: pathlib.Path = metafile
    self.meta: scrape_repos_pb2.GitHubRepoMetadata = pbutil.FromFile(
        metafile, scrape_repos_pb2.GitHubRepoMetadata())
    self.name: str = f'{self.meta.owner}_{self.meta.name}'
    self.clone_dir: pathlib.Path = metafile.parent / self.name
    self.index_dir = (
        pathlib.Path(str(metafile.parent.parent) + '.index') / self.name)

  def IsCloned(self) -> bool:
    """Return whether the repo has been cloned."""
    return (self.clone_dir / '.git').is_dir()

  def IsIndexed(self) -> bool:
    """Return whether the repo has been indexed."""
    return self.IsCloned() and (self.index_dir / 'DONE.txt').is_file()

  def Clone(self) -> 'GitHubRepo':
    """Clone the repo."""
    if self.IsCloned():
      return self

    raise NotImplementedError

  def Index(self,
            indexers: typing.List[scrape_repos_pb2.ContentFilesImporterConfig],
            pool: multiprocessing.Pool) -> 'GitHubRepo':
    """Index the repo."""
    if self.IsIndexed():
      return self

    self.index_dir.mkdir(parents=True, exist_ok=True)
    for indexer in indexers:
      self._IndexPattern(indexer, pool)
    (self.index_dir / 'DONE.txt').touch()

  def _IndexPattern(self, indexer: scrape_repos_pb2.ContentFilesImporterConfig,
                    pool: multiprocessing.Pool) -> 'GitHubRepo':
    """Index the repo."""
    pattern = indexer.source_code_pattern
    pattern = (f'{self.clone_dir}/{pattern[1:]}'
               if pattern[0] == '^' else
               f'{self.clone_dir}/{pattern}')
    cmd = ['find', str(self.clone_dir), '-type', 'f', '-regex', pattern, '-not',
           '-path', '*/.git/*']
    logging.debug('$ %s', ' '.join(cmd))
    paths = subprocess.check_output(
        cmd, universal_newlines=True).rstrip().split('\n')
    if len(paths) == 1 and not paths[0]:
      logging.debug('No files to import from %s', self.clone_dir)
      return self
    logging.info("Importing %s files from %s ...",
                 humanize.intcomma(len(paths)), self.name)
    all_files_relpaths = public.GetAllFilesRelativePaths(self.clone_dir)
    jobs = [
      scrape_repos_pb2.ImportWorker(
          clone_from_url=self.meta.clone_from_url,
          clone_dir=str(self.clone_dir),
          abspath=p,
          all_files_relpaths=all_files_relpaths,
          preprocessors=indexer.preprocessor,
      ) for p in paths
    ]
    bar = progressbar.ProgressBar(max_value=len(jobs))
    for outputs in bar(pool.imap_unordered(ImportWorker, jobs)):
      for output in outputs:
        proto_path = self.index_dir / (output.sha256_hex + '.pbtxt')
        pbutil.ToFile(output.ToProto(), proto_path)

  def ContentFiles(self) -> typing.Iterable[scrape_repos_pb2.ContentFile]:
    """Return an iterator over all contentfiles in the repo."""
    if self.IsIndexed():
      return (pbutil.FromFile(f, scrape_repos_pb2.ContentFile())
              for f in self.index_dir.iterdir() if f.name != 'DONE.txt')
    else:
      return []


def ImportWorker(
    job: scrape_repos_pb2.ImportWorker
) -> typing.List[contentfiles.ContentFile]:
  """Import content files."""
  relpath = job.abspath[len(str(job.clone_dir)) + 1:]
  outputs: typing.List[contentfiles.ContentFile] = []
  texts = preprocessors.Preprocess(pathlib.Path(job.clone_dir), relpath,
                                   job.all_files_relpaths, job.preprocessors)
  for i, text in enumerate(texts):
    try:
      sha256 = hashlib.sha256(text.encode('utf-8'))
      outputs.append(contentfiles.ContentFile(
          clone_from_url=job.clone_from_url,
          relpath=relpath,
          artifact_index=i,
          sha256=sha256.digest(),
          charcount=len(text),
          linecount=len(text.split('\n')),
          text=text))
    except UnicodeDecodeError:
      logging.warning('Failed to decode %s', relpath)
  return outputs