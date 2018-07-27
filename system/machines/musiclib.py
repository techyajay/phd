"""Dpack creates structured packages of data files.

Dpack creates an archive of a directory with a manifest. The manifest describes
each of the files within the directory. Use dpack to create consistent and well
documented collections of data files.
"""

import sys

import pathlib
from absl import app
from absl import flags

from lib.dpack import dpack
from phd.lib.labm8 import fs

FLAGS = flags.FLAGS

flags.DEFINE_string('musiclib', None,
                    'The path of the music library.')
flags.DEFINE_bool('update_musiclib_manifests', False, '')

# The --package argument points to either a directory or an archive file.
flags.register_validator(
    'musiclib',
    # Flags validation occurs whenever this file is imported. During unit
    # testing we have no value for this flag, so the validator should only
    # run if the flag is present.
    lambda path: pathlib.Path(path).is_dir() if path else True,
    message='--musiclib must be a directory.')

# A list of filename patterns to exclude from all data packages.
ALWAYS_EXCLUDE_PATTERNS = [
  '.*',
]


def GetAlbumDirectories(directory: pathlib.Path):
  """TODO."""
  okay = True
  for subdir in fs.lsdirs(directory, recursive=True):
    subdir = pathlib.Path(subdir)
    depth = len(pathlib.Path(subdir).parts)
    if depth == 2:
      if FLAGS.update_musiclib_manifests:
        contents = dpack.GetFilesInDirectory(directory / subdir, ['.*'])
        dpack.InitManifest(directory / subdir, contents, update=True)
      else:
        if not dpack.VerifyManifest(directory / subdir):
          okay = False

  return okay


def main(argv) -> None:
  """Main entry point."""
  # Validate flags.
  if len(argv) > 1:
    raise app.UsageError('Too many command-line arguments.')

  if not FLAGS.musiclib:
    raise app.UsageError('--musiclib argument is required.')

  musiclib = pathlib.Path(FLAGS.musiclib)
  if not GetAlbumDirectories(musiclib / 'Music Library/Music'):
    sys.exit(1)


if __name__ == '__main__':
  app.run(main)
