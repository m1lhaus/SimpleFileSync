# Simple File Sync

SimpleFileSync is rsync-like script written in Python. Rsync is a great tool, however when doing a sync of two network drives (NFS) on very poor connection (response time in particular), I found rsync as very slow tool. For example just to list all files for sync in dry-run mode rsync needs more than 30 mins in my scenario. And since I find work with those shared drives from Jupyter notebook with ThreadPoolExecutor as reasonably fast, I decided to implement a simple script that will cover my small usecase. That is to perform fast sync between two file trees. With this script on my usecase, listing a summary diff table of actions to be done takes less than 5 seconds.

## Requirements

As a 3rd-party package only [colorama](https://pypi.org/project/colorama/) is used. 

```
pip install colorama
```

## Usage

```
> python sfsync.py --help

usage: sfsync.py [-h]
                 [--exclude-folder-names EXCLUDE_FOLDER_NAMES [EXCLUDE_FOLDER_NAMES ...]]
                 [--exclude-file-ext EXCLUDE_FILE_EXT [EXCLUDE_FILE_EXT ...]]
                 [--one-direction-sync] [--delete-orphans] [--prefer-source]
                 [--summary] [--dry-run]
                 source target

Simple file sync script is rsync-like tool written in Python. It can do simple
both-ways sync between source and target file tress. Files are compares just
based on their file size and last modification date. Optionally only one-way
sync can be used, files that are on target but not in source can be removed or
you can force source dir to be considered as the latest and overwrite anything
that differs on the target.

positional arguments:
  source                Source directory - from where to sync.
  target                Target directory - with which to sync.

optional arguments:
  -h, --help            show this help message and exit
  --exclude-folder-names EXCLUDE_FOLDER_NAMES [EXCLUDE_FOLDER_NAMES ...]
                        Folder names (not paths) that should be excluded. E.g.
                        images, dumps, cache, etc.
  --exclude-file-ext EXCLUDE_FILE_EXT [EXCLUDE_FILE_EXT ...]
                        File extensions that should be excluded. Eg. .png,
                        .jpg, .ini, etc.
  --one-direction-sync  Flag whether to sync files only from source -> target
                        and not in both directions.
  --delete-orphans      Flag used in combination with '--one-direction-sync'
                        to delete all differences on the target. Therefore all
                        files that are on target and not on source will be
                        deleted. The result is the same as when you would
                        delete the target folder first and then start coping
                        the source dir, but more effective of course.
  --prefer-source       When set, source file is always considered to be the
                        latest one.
  --summary             Print summary table with files to be changed. is
                        recommended to be used together with '--dry-run' to
                        find out first, what will be done.
  --dry-run             Do not copy / remove anything, just simulate actions.

```

## Examples


Simply list two file trees and create diff summary for a check:

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target --summary --dry-run
```

Sync those trees with no summary table:

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target
```

### Exlude stuff:


We found out somebody dumped there 60 gigs of images, we don't want that:

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target --summary --dry-run \
        --exclude-folder-names images dumps
```

or

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target --summary --dry-run \
        --exclude-file-ext .png .jpg 
```

### Sync in one direction


We want to sync just stuff from source to target:

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target --summary --dry-run \
        --one-direction-sync
```

Lets just sync just stuff from source to target and we want to remove additional files (leftovers) from the target. For example we removed some files/folders from the source and we don't want to sync them back from target:

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target --summary --dry-run \
        --one-direction-sync --delete-orphans
```

What if we messed up our local copy of files due to some accidental write and we want to revert those changes by overwriting those files from out remote storage. But now local files have newer modification data. Fortunately, we can force this update so instead of copying all from the server, we revert just those few files:

```
> python sfsync.py \\net_pc1\\source \\net_pc2\target --summary --dry-run \
        --prefer-source
```

## Licence

GNU General Public License v3.0