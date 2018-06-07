"""Run a baseline."""
import pathlib
import typing

from absl import app
from absl import flags
from absl import logging

from deeplearning.clgen import clgen
from deeplearning.clgen.corpuses import corpuses
from deeplearning.clgen.proto import clgen_pb2
from deeplearning.clgen.proto import corpus_pb2
from deeplearning.clgen.proto import model_pb2
from deeplearning.clgen.proto import sampler_pb2
from experimental.deeplearning.polyglot import get_instances
from lib.labm8 import crypto
from lib.labm8 import lockfile
from lib.labm8 import pbutil


FLAGS = flags.FLAGS

flags.DEFINE_string('corpus', None, 'Path to corpus config.')
flags.DEFINE_string('model', None, 'Path to model config.')
flags.DEFINE_string('sampler', None, 'Path to sampler config.')
flags.DEFINE_string('working_dir',
                    '/mnt/cc/data/experimental/polyglot/baselines',
                    'Path to CLgen working directory')
flags.DEFINE_integer('output_corpus_size', 5000,
                     'The minimum number of samples to generate in the output'
                     'corpus.')


def IsElligible(instance: clgen.Instance):
  """Return whether an instance is elligible for training or sampling."""
  if instance.model.lock.islocked:
    return False
  sample_dir = instance.model.SamplerCache(instance.sampler)
  sample_lock = lockfile.LockFile(sample_dir / 'LOCK')
  if sample_lock.islocked:
    return False
  return True


# TODO(cec): Generalize for other symmetrical tokens.
def ExtractAllSubsamples(text: str, start_text: str, left_char: str,
                         right_char: str) -> typing.List[str]:
  """Extract all subsamples from text.

  Find all substrings in text which begin with start_text and have a symmetrical
  balance of left and right chars.
  """
  out = []
  start_index = text.find(start_text)
  while start_index >= 0:
    started = False
    depth = 0
    j = 0
    for j in range(start_index, len(text)):
      if text[j] == left_char:
        depth += 1
        started = True
      elif text[j] == right_char:
        depth -= 1
      if started and not depth:
        break
    out.append(text[start_index:j + 1])
    start_index = text.find(start_text, start_index + 1)
  return out


def SampleModel(instance: clgen.Instance) -> None:
  """Make --output_corpus_size samples from model."""
  logging.info('Training and sampling the CLgen model ...')
  target_samples = FLAGS.output_corpus_size
  num_samples = 0
  sample_dir = instance.model.SamplerCache(instance.sampler)
  if sample_dir.is_dir():
    num_samples = len(list(sample_dir.iterdir()))
  logging.info('Need to generate %d samples in %s',
               max(target_samples - num_samples, 0), sample_dir)
  while num_samples < target_samples:
    instance.Sample(min_num_samples=target_samples - num_samples)
    num_samples = len(list(sample_dir.iterdir()))


def CreateOutputCorpus(instance: clgen.Instance) -> corpuses.Corpus:
  """Create a directory of contentfiles from the samples we just made."""
  out_dir = pathlib.Path(
      str(instance.model.SamplerCache(instance.sampler)) + '.postprocessed')
  out_dir.mkdir(exist_ok=True)
  sample_dir = instance.model.SamplerCache(instance.sampler)
  logging.info('Creating output corpus at %s from sample dir %s',
               out_dir, sample_dir)
  for sample_path in sample_dir.iterdir():
    sample = pbutil.FromFile(sample_dir / sample_path, model_pb2.Sample())
    out_samples = ExtractAllSubsamples(
        sample.text, instance.sampler.start_text, '{', '}')
    for out_sample in out_samples:
      sha256 = crypto.sha256_str(out_sample)
      with open(out_dir / (sha256 + '.txt'), 'w') as f:
        f.write(out_sample)
  logging.info('Created output corpus of %s file',
               len(list(out_dir.iterdir())))
  output_corpus_config = corpus_pb2.Corpus()
  output_corpus_config.CopyFrom(instance.model.corpus.config)
  output_corpus_config.local_directory = str(out_dir)
  output_corpus = corpuses.Corpus(output_corpus_config)
  logging.info('Pre-processing the output samples as corpus: %s',
               output_corpus.hash)
  output_corpus.Create()
  return output_corpus


def PostprocessSampleCorpus(instance: clgen.Instance):
  """Create a corpus from the model samples and pre-process."""
  sample_dir = instance.model.SamplerCache(instance.sampler)

  # Read the sample protos and write them to a directory of content files.
  contentfiles_dir = pathlib.Path(str(sample_dir) + '.contentfiles')
  contentfiles_dir.mkdir(exist_ok=True)
  logging.info('Writing output contentfiles to %s', contentfiles_dir)
  if len(list(contentfiles_dir.iterdir())) != len(list(sample_dir.iterdir())):
    for proto_path in sample_dir.iterdir():
      sample = pbutil.FromFile(proto_path, model_pb2.Sample())
      with open(contentfiles_dir / proto_path.name, 'w') as f:
        f.write(sample.text)

  logging.info('Creating output corpus')
  output_corpus_config = corpus_pb2.Corpus()
  output_corpus_config.CopyFrom(instance.model.corpus.config)
  output_corpus_config.local_directory = str(contentfiles_dir)
  output_corpus = corpuses.Corpus(output_corpus_config)
  output_corpus.Create()
  return output_corpus


def main(argv):
  """Main entry point."""
  if len(argv) > 1:
    raise app.UsageError("Unknown arguments: '{}'.".format(' '.join(argv[1:])))

  candidate_instances = get_instances.GetInstances()

  corpus = pbutil.FromFile(pathlib.Path(FLAGS.corpus), corpus_pb2.Corpus())
  model = pbutil.FromFile(pathlib.Path(FLAGS.model), model_pb2.Model())
  model.corpus.CopyFrom(corpus)
  sampler = pbutil.FromFile(pathlib.Path(FLAGS.sampler), sampler_pb2.Sampler())

  config = clgen_pb2.Instance(model=model, sampler=sampler)
  config.working_dir = FLAGS.working_dir
  instance = clgen.Instance(config)

  with instance.Session():
    SampleModel(instance)
    # CreateOutputCorpus(instance)
    PostprocessSampleCorpus(instance)


if __name__ == '__main__':
  app.run(main)