"""This file defines a database for pre-preprocessed content files."""
import binascii
import contextlib
import datetime
import hashlib
import multiprocessing
import os
import pathlib
import subprocess
import tempfile
import time
import typing

import humanize
import progressbar
import sqlalchemy as sql
from absl import flags
from absl import logging
from sqlalchemy.ext import declarative

from deeplearning.clgen import errors
from deeplearning.clgen.preprocessors import preprocessors
from deeplearning.clgen.proto import corpus_pb2
from deeplearning.clgen.proto import internal_pb2
from lib.labm8 import fs
from lib.labm8 import sqlutil


FLAGS = flags.FLAGS

Base = declarative.declarative_base()


class Meta(Base):
  __tablename__ = 'meta'

  key: str = sql.Column(sql.String(1024), primary_key=True)
  value: str = sql.Column(sql.String(1024), nullable=False)


class PreprocessedContentFile(Base):
  __tablename__ = 'preprocessed_contentfiles'

  id: int = sql.Column(sql.Integer, primary_key=True)
  # Relative path of the input file within the content files.
  input_relpath: str = sql.Column(sql.String(4096), nullable=False, unique=True)
  # Checksum of the input file.
  input_sha256: str = sql.Column(sql.Binary(32), nullable=False)
  # Checksum of the preprocessed file.
  sha256: str = sql.Column(sql.Binary(32), nullable=False, index=True)
  charcount = sql.Column(sql.Integer, nullable=False)
  linecount = sql.Column(sql.Integer, nullable=False)
  text: str = sql.Column(sql.UnicodeText(), nullable=False)
  # True if pre-processing succeeded, else False.
  preprocessing_succeeded: bool = sql.Column(sql.Boolean, nullable=False)
  # The number of milliseconds pre-preprocessing took.
  preprocess_time_ms: int = sql.Column(sql.Integer, nullable=False)
  date_added: datetime.datetime = sql.Column(sql.DateTime, nullable=False,
                                             default=datetime.datetime.utcnow)

  @property
  def input_sha256_hex(self) -> str:
    """Return the 64 character hexadecimal representation of input_sha256."""
    return binascii.hexlify(self.input_sha256).decode('utf-8')

  @property
  def sha256_hex(self) -> str:
    """Return the 64 character hexadecimal representation of sha256."""
    return binascii.hexlify(self.sha256).decode('utf-8')

  @classmethod
  def FromContentFile(
      cls, contentfile_root: pathlib.Path, relpath: pathlib.Path,
      preprocessors_: typing.List[str]) -> 'PreprocessedContentFile':
    """Instantiate a PreprocessedContentFile."""

    with open(contentfile_root / relpath) as f:
      input_text = f.read()
    start_time = time.time()
    preprocessing_succeeded = False
    try:
      text = preprocessors.Preprocess(input_text, preprocessors_)
      preprocessing_succeeded = True
    except errors.BadCodeException as e:
      text = str(e)
      # An intentionally broad exception catch here to catch whatever's left.
    except errors.InternalError as e:
      text = f'!!INTERNAL ERROR!! {e}'
    end_time = time.time()
    preprocess_time_ms = int((end_time - start_time) * 1000)
    return cls(
        input_relpath=relpath,
        input_sha256=GetFileSha256(contentfile_root / relpath),
        sha256=hashlib.sha256(text.encode('utf-8')).digest(),
        charcount=len(text),
        linecount=len(text.split('\n')),
        text=text,
        preprocessing_succeeded=preprocessing_succeeded,
        preprocess_time_ms=preprocess_time_ms,
        date_added=datetime.datetime.utcnow(),
    )


def PreprocessorWorker(
    job: internal_pb2.PreprocessorWorker) -> PreprocessedContentFile:
  return PreprocessedContentFile.FromContentFile(
      pathlib.Path(job.contentfile_root), job.relpath, job.preprocessors)


class PreprocessedContentFiles(sqlutil.Database):
  """A database of pre-processed contentfiles."""

  def __init__(self, path: pathlib.Path):
    super(PreprocessedContentFiles, self).__init__(path, Base)

  def Create(self, config: corpus_pb2.Corpus) -> bool:
    with self.Session() as session:
      if self.IsDone(session):
        return False
      else:
        self.Import(session, config)
        self.SetDone(session)
        session.commit()
        return True

  def IsDone(self, session: sqlutil.Database.session_t):
    if session.query(Meta).filter(Meta.key == 'done').first():
      return True
    else:
      return False

  def SetDone(self, session: sqlutil.Database.session_t):
    session.add(Meta(key='done', value='yes'))

  def Import(self, session: sqlutil.Database.session_t,
             config: corpus_pb2.Corpus) -> None:
    start_time = time.time()
    with self.GetContentFileRoot(config) as contentfile_root:
      relpaths = set(self.GetImportRelpaths(contentfile_root))
      done = set(
          [x[0] for x in session.query(PreprocessedContentFile.input_relpath)])
      todo = relpaths - done
      logging.info('Preprocessing %s of %s content files',
                   humanize.intcomma(len(todo)),
                   humanize.intcomma(len(relpaths)))
      jobs = [
        internal_pb2.PreprocessorWorker(
            contentfile_root=str(contentfile_root),
            relpath=t, preprocessors=config.preprocessor)
        for t in todo]
      pool = multiprocessing.Pool()
      bar = progressbar.ProgressBar(max_value=len(jobs))
      last_commit = time.time()
      for preprocessed_cf in bar(pool.imap_unordered(
          PreprocessorWorker, jobs)):
        session.add(preprocessed_cf)
        current_time = time.time()
        if current_time - last_commit > 10:
          session.commit()
          last_commit = current_time
      logging.info('Preprocessed %s content files in %s ms',
                   humanize.intcomma(len(todo)),
                   humanize.intcomma(int((time.time() - start_time) * 1000)))
      # TODO(cec): Report num successful preprocessed and discard rate.

  @contextlib.contextmanager
  def GetContentFileRoot(self, config: corpus_pb2.Corpus) -> pathlib.Path:
    if config.HasField('local_directory'):
      yield pathlib.Path(ExpandConfigPath(config.local_directory))
    elif config.HasField('local_tar_archive'):
      with tempfile.TemporaryDirectory(prefix='clgen_corpus_') as d:
        start_time = time.time()
        cmd = ['tar', '-xf', str(ExpandConfigPath(config.local_tar_archive)),
               '-C', d]
        subprocess.check_call(cmd)
        logging.info('Unpacked %s in %s ms',
                     ExpandConfigPath(config.local_tar_archive).name,
                     humanize.intcomma(int((time.time() - start_time) * 1000)))
        yield pathlib.Path(d)
    else:
      raise NotImplementedError

  def GetImportRelpaths(
      self, contentfile_root: pathlib.Path) -> typing.List[pathlib.Path]:
    with fs.chdir(contentfile_root):
      return subprocess.check_output(['find', '.', '-type', 'f']).decode(
          'utf-8').strip().split('\n')


def ExpandConfigPath(path: str) -> pathlib.Path:
  return pathlib.Path(os.path.expandvars(path)).expanduser().absolute()


def GetFileSha256(path: pathlib.Path):
  with open(path, 'rb') as f:
    return hashlib.sha256(f.read()).digest()