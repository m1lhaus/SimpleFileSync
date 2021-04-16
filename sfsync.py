#!/usr/bin/env python

import os
import shutil
import time
import argparse
import sys
import math
import colorama

from colorama import Fore

from concurrent.futures import ThreadPoolExecutor


def _copyfileobj_patched(fsrc, fdst, length=0):
    """
    Custom implementation of copyfileobj() method from shutil that uses custom buffer size.
    Windows don't really like small buffers so we can gain speedup by using bigger buffer for bigger file (less overhead).
    Python 3.8+ uses as default buffer size 1MB. But here we scale beyond 1MB as src file grows biggers.
    """
    file_size_B = os.fstat(fsrc.fileno()).st_size

    """
    Applied funtion:
    1.0e+00 B -> 1024.0 kB
    1.0e+01 B -> 1024.0 kB
    1.0e+02 B -> 1024.0 kB
    1.0e+03 B -> 1024.0 kB
    1.0e+04 B -> 1024.0 kB
    1.0e+05 B -> 1024.0 kB
    1.0e+06 B -> 1024.0 kB   ... 1MB file has 1MB buffer
    1.0e+07 B -> 3331.6 kB
    1.0e+08 B -> 6733.2 kB
    1.0e+09 B -> 10134.9 kB  ... 1GB file has 10MB buffer
    1.0e+10 B -> 13536.5 kB
    """
    buffer_size_kB = (math.log2(max(file_size_B, 1)) - 20) * 1024
    buffer_size_kB = max(buffer_size_kB, 1024)

    buffer_size_B = int(buffer_size_kB*1024)
    if length > 0:
        buffer_size_B = min(length, buffer_size_B)

    # Localize variable access to minimize overhead.
    fsrc_read = fsrc.read
    fdst_write = fdst.write
    while True:
        buf = fsrc_read(buffer_size_B)
        if not buf:
            break
        fdst_write(buf)


class Action:
    COPY_SOURCE_TO_TARGET = 0
    COPY_TARGET_TO_SOURCE = 1
    REMOVE_TARGET = 2

    labels = {
        0: Fore.GREEN + "SOURCE --> TARGET" + Fore.RESET,
        1: Fore.YELLOW + "TARGET --> SOURCE" + Fore.RESET,
        2: Fore.RED + "REMOVE_ON_TARGET" + Fore.RESET
    }


class PathType:
    FILE = 0
    FOLDER = 1


class PathInfo:
    def __init__(self, pathtype, abspath, filesize=None, modtime=None):
        self.pathtype = pathtype
        self.abspath = abspath
        self.filesize = filesize
        self.modtime = modtime


def list_folder_tree(filepath):
    """
    Method recursively lists all files from given root folder.
    Optionally folder names from args.exclude_folder_names can be skipped.
    Optionally file with file extensions args.exclude_file_ext can be skipped.
    """
    file_list = []
    folder_list = []
    for root, folders, files in os.walk(filepath):
        folders[:] = [dirname for dirname in folders if dirname not in args.exclude_folder_names]       # this will make os.walk to skip those folders
        for ffile in files:
            if not ffile.lower().endswith(args.exclude_file_ext):
                file_list.append(os.path.join(root, ffile))
        folder_list.append(root)

    return folder_list, file_list


def get_file_info(filepath):
    """
    Loads file site and last modification date for given filepath.
    """
    size = os.path.getsize(filepath)
    mt = os.path.getmtime(filepath)

    return filepath, size, mt


def merge_trees(source_tree, target_tree, source_folders, target_folders):
    """
    Method takes file lis of source folder and target folder and merges them together into one dict,
    where key is the relpath to the file (rel to source/target root).
    """
    index = {}
    for path, size, mt in source_tree:
        relpath = os.path.relpath(path, args.source)
        source_info = PathInfo(PathType.FILE, path, filesize=size, modtime=mt)
        index[relpath] = (source_info, None)
    for path in source_folders:
        relpath = os.path.relpath(path, args.source)
        source_info = PathInfo(PathType.FOLDER, path)
        index[relpath] = (source_info, None)

    for path, size, mt in target_tree:
        relpath = os.path.relpath(path, args.target)
        source_info, _ = index.get(relpath, (None, None))
        target_info = PathInfo(PathType.FILE, path, filesize=size, modtime=mt)
        index[relpath] = (source_info, target_info)
    for path in target_folders:
        relpath = os.path.relpath(path, args.target)
        source_info, _ = index.get(relpath, (None, None))
        target_info = PathInfo(PathType.FOLDER, path)
        index[relpath] = (source_info, target_info)

    return index


def get_sync_direction(index):
    """
    Based on source and target file properties (does it exists?, their last modification time and size) and input args the method decides
    sync direction. I.e. whether to copy file from source to target or from target to source - or whether to remove target file.
    """
    what_to_do_index = {}

    for relpath, (source_info, target_info) in index.items():

        # source file does not exist
        if source_info is None:
            source_info = PathInfo(target_info.pathtype, os.path.join(args.source, relpath))
            if not args.one_direction_sync:
                what_to_do_index[relpath] = (Action.COPY_TARGET_TO_SOURCE, source_info, target_info)
            elif args.delete_orphans:
                what_to_do_index[relpath] = (Action.REMOVE_TARGET, source_info, target_info)
            else:
                continue

        # target file does not exist
        elif target_info is None:
            target_info = PathInfo(source_info.pathtype, os.path.join(args.target, relpath))
            what_to_do_index[relpath] = (Action.COPY_SOURCE_TO_TARGET, source_info, target_info)

        # both files exist, do a sync
        elif (source_info.pathtype == PathType.FILE) and (target_info.pathtype == PathType.FILE):
            # files are the same
            if (source_info.modtime == target_info.modtime) and (source_info.filesize == target_info.filesize):
                continue

            # source is older
            elif source_info.modtime < target_info.modtime:
                what_to_do_index[relpath] = (Action.COPY_TARGET_TO_SOURCE if not args.prefer_source else Action.COPY_SOURCE_TO_TARGET, source_info, target_info)

            # target is older
            elif source_info.modtime > target_info.modtime:
                what_to_do_index[relpath] = (Action.COPY_SOURCE_TO_TARGET, source_info, target_info)

            else:
                raise Exception(F"File {relpath} have same createdTime, but different file size: {source_info.filesize} != {target_info.filesize}! One of them might be corrupted!")

    return what_to_do_index


def print_summary(index):
    """
    Prints summary table in format:
    Sync direction, Last modification data, File sizes, Relative path
    """

    def sizeof_fmt(num, suffix='B'):
        """
        Converts byte site to human readable file size.
        """
        for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
            if abs(num) < 1024.0:
                return "%7.3f%-3s" % (num, unit+suffix)
            num /= 1024.0
        return "%.1f%-3s" % (num, 'Yi'+suffix)

    print()
    print(Fore.GREEN + "SOURCE:" + Fore.RESET, args.source)
    print(Fore.YELLOW + "TARGET:" + Fore.RESET, args.target)
    print()

    print('{:17}'.format('Sync direction') + " | " + '{:41}'.format('Last modification time') + " | " + '{:23}'.format('File size') + " | Relative path:")
    print("-"*200)

    for relpath, (direction, source_info, target_info) in index.items():
        source_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(source_info.modtime)) if source_info.modtime is not None else "xxxx-xx-xx 00:00:00"
        target_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(target_info.modtime)) if target_info.modtime is not None else "xxxx-xx-xx 00:00:00"

        direction_str = Action.labels[direction]

        # figure out time operator
        if source_info.modtime is None or target_info.modtime is None:
            time_op = "?"
        elif source_info.modtime > target_info.modtime:
            time_op = ">"
        else:
            time_op = "<"

        # figure out size operator
        source_info.filesize = source_info.filesize if source_info.filesize is not None else 0.0
        target_info.filesize = target_info.filesize if target_info.filesize is not None else 0.0
        if source_info.filesize > target_info.filesize:
            size_op = ">"
        elif source_info.filesize == target_info.filesize:
            size_op = "="
        else:
            size_op = "<"

        print(F"{direction_str:27s} | {source_time_str} {time_op} {target_time_str} | {sizeof_fmt(source_info.filesize)} {size_op} {sizeof_fmt(target_info.filesize)} | {relpath}")
    print()


def remove_file(filepath):
    if not args.dry_run:
        os.remove(filepath)
    else:
        print(F"DryRun: remove {filepath}")


def remove_dir(folderpath):
    if not args.dry_run:
        shutil.rmtree(folderpath, ignore_errors=True)
    else:
        print(F"DryRun: remove {folderpath}")


def make_dir(folderpath):
    if not args.dry_run:
        os.makedirs(folderpath, exist_ok=True)
    else:
        print(F"DryRun: makedir {folderpath}")


def copy_file(src, dst):
    if not args.dry_run:
        dirpath = os.path.dirname(dst)
        if not os.path.isdir(dirpath):
            os.makedirs(dirpath, exist_ok=True)

        shutil.copy2(src, dst)
    else:
        print(F"DryRun: copy {src} \n\t\t---> {dst}")


def execute_file_action(action: Action, source_info: PathInfo, target_info: PathInfo):
    if action == Action.REMOVE_TARGET:
        remove_file(target_info.abspath)
    elif action == Action.COPY_SOURCE_TO_TARGET:
        copy_file(source_info.abspath, target_info.abspath)
    elif action == Action.COPY_TARGET_TO_SOURCE:
        copy_file(target_info.abspath, source_info.abspath)
    else:
        raise Exception(F"Unsupported action ID: {action}")


def execute_folder_action(action, source_info, target_info):
    if action == Action.REMOVE_TARGET:
        remove_dir(target_info.abspath)
    elif action == Action.COPY_SOURCE_TO_TARGET:
        make_dir(target_info.abspath)
    elif action == Action.COPY_TARGET_TO_SOURCE:
        make_dir(source_info.abspath)
    else:
        raise Exception(F"Unsupported action ID: {action}")


def execute_action(data):
    action, source_info, target_info = data
    if source_info.pathtype == PathType.FILE:
        execute_file_action(action, source_info, target_info)
    else:
        execute_folder_action(action, source_info, target_info)
    return True


def main():
    with ThreadPoolExecutor(2) as pool:
        (source_folders, source_files), (target_folders, target_files) = list(pool.map(list_folder_tree, [args.source, args.target]))

    with ThreadPoolExecutor() as pool:
        source_files_info = list(pool.map(get_file_info, source_files))
        target_files_info = list(pool.map(get_file_info, target_files))

    index = merge_trees(source_files_info, target_files_info, source_folders, target_folders)
    what_to_do = get_sync_direction(index)

    if args.summary:
        print_summary(what_to_do)

    t0 = time.time()
    print(F"Coping started at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

    workers = []
    num_workders = 1 if args.dry_run else args.max_workers
    with ThreadPoolExecutor(num_workders) as pool:
        for data in what_to_do.values():
            workers.append(pool.submit(execute_action, data))

        n_all = len(workers)
        finished = False
        while not finished:
            n_finished = sum([fut.done() for fut in workers])
            finished = (n_finished == n_all)
            sys.stdout.write(F"\r\rWork in progress, finished {n_finished}/{n_all}")
            sys.stdout.flush()
            time.sleep(0.5)

        results = [fut.result() for fut in workers]

    if all(results):
        print()
        print(F"Done at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")
        print(F"Total time elapsed: {time.time() - t0} sec")


if __name__ == '__main__':
    shutil.copyfileobj = _copyfileobj_patched       # custom copy function since Windows don't like small cache for big files to copy

    arg_parser = argparse.ArgumentParser(description="Simple file sync script is rsync-like tool written in Python. "
                                                     "It can do simple both-ways sync between source and target file tress. "
                                                     "Files are compares just based on their file size and last modification date. "
                                                     "Optionally only one-way sync can be used, files that are on target but not in source can be removed or you can force source "
                                                     "dir to be considered as the latest and overwrite anything that differs on the target.")
    arg_parser.add_argument("source", type=str, help="Source directory - from where to sync.")
    arg_parser.add_argument("target", type=str, help="Target directory - with which to sync.")
    arg_parser.add_argument("--exclude-folder-names", type=str, nargs="+", default=(), help='Folder names (not paths) that should be excluded. E.g. images, dumps, cache, etc.')
    arg_parser.add_argument("--exclude-file-ext", type=str, nargs="+", default=(), help="File extensions that should be excluded. Eg. .png, .jpg, .ini, etc.")
    arg_parser.add_argument('--one-direction-sync', action="store_true", help="Flag whether to sync files only from source -> target and not in both directions.")
    arg_parser.add_argument('--delete-orphans', action="store_true", help="Flag used in combination with '--one-direction-sync' to delete all differences on the target. "
                                                                          "Therefore all files that are on target and not on source will be deleted. "
                                                                          "The result is the same as when you would delete the target folder first and then start coping the "
                                                                          "source dir, but more effective of course.")
    arg_parser.add_argument('--prefer-source', action="store_true", help="When set, source file is always considered to be the latest one.")
    arg_parser.add_argument('--summary', action="store_true", help="Print summary table with files to be changed. "
                                                                   " is recommended to be used together with '--dry-run' to find out first, what will be done.")
    arg_parser.add_argument('--dry-run', action="store_true", help="Do not copy / remove anything, just simulate actions.")
    arg_parser.add_argument('--max-workers', type=int, default=None, help="Number of parallel copy jobs (threads). As default min(32, os.cpu_count() + 4) is used.")
    args = arg_parser.parse_args()
    args.exclude_folder_names = tuple(args.exclude_folder_names)
    args.exclude_file_ext = tuple([ext.lower() for ext in args.exclude_file_ext])

    if not os.path.isdir(args.source):
        raise Exception(f"Source folder path {args.source} does not exist!")
    if not os.path.isdir(args.target):
        raise Exception(f"Target folder path {args.target} does not exist!")
    if not args.one_direction_sync and args.delete_orphans:
        raise Exception("Flag --delete-orphans can be used only in combination with --one-direction-sync!")

    colorama.init()
    main()
