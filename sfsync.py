#!/usr/bin/env python

import os
import shutil
import time
import argparse
import sys
import colorama

from colorama import Fore

from concurrent.futures import ThreadPoolExecutor


class Action:
    COPY_SOURCE_TO_TARGET = 0
    COPY_TARGET_TO_SOURCE = 1
    REMOVE_TARGET = 2

    labels = {
        0: Fore.GREEN + "SOURCE --> TARGET" + Fore.RESET,
        1: Fore.YELLOW + "TARGET --> SOURCE" + Fore.RESET,
        2: Fore.RED + "REMOVE_ON_TARGET" + Fore.RESET
    }


def list_files(filepath):
    """
    Method recursively lists all files from given root folder.
    Optionally folder names from args.exclude_folder_names can be skipped.
    Optionally file with file extensions args.exclude_file_ext can be skipped.
    """
    output = []
    for root, folders, files in os.walk(filepath):
        folders[:] = [d for d in folders if d not in args.exclude_folder_names]
        for ffile in files:
            if not ffile.endswith(args.exclude_file_ext):
                output.append(os.path.join(root, ffile))
    return output


def get_file_info(filepath):
    """
    Loads file site and last modification date for given filepath.
    """
    size = os.path.getsize(filepath)
    mt = os.path.getmtime(filepath)

    return filepath, size, mt


def merge_trees(source_tree, target_tree):
    """
    Method takes file lis of source folder and target folder and merges them together into one dict,
    where key is the relpath to the file (rel to source/target root).
    """
    index = {}
    for path, size, mt in source_tree:
        relpath = os.path.relpath(path, args.source)
        index[relpath] = [(path, size, mt), None]
    for path, size, mt in target_tree:
        relpath = os.path.relpath(path, args.target)
        tmp = index.get(relpath, [None, None])
        tmp[1] = (path, size, mt)
        index[relpath] = tmp
    return index


def get_sync_direction(index):
    """
    Based on source and target file properties (does it exists?, their last modification time and size) and input args the method decides
    sync direction. I.e. whether to copy file from source to target or from target to source - or whether to remove target file.
    """
    copy_index = {}

    for relpath, (source_info, target_info) in index.items():

        # source file does not exist
        if source_info is None:
            source_info = (os.path.join(args.source, relpath), None, None)
            if not args.one_direction_sync:
                copy_index[relpath] = (Action.COPY_TARGET_TO_SOURCE, source_info, target_info)
            elif args.delete_orphans:
                copy_index[relpath] = (Action.REMOVE_TARGET, source_info, target_info)
            else:
                continue

        # target file does not exist
        elif target_info is None:
            target_info = (os.path.join(args.target, relpath), None, None)
            copy_index[relpath] = (Action.COPY_SOURCE_TO_TARGET, source_info, target_info)

        # both files exist, do a sync
        else:
            _, source_size, source_mt = source_info
            _, target_size, target_mt = target_info

            # files are the same
            if (source_mt == target_mt) and (source_size == target_size):
                continue

            # source is older
            elif source_mt < target_mt:
                copy_index[relpath] = (Action.COPY_TARGET_TO_SOURCE if not args.prefer_source else Action.COPY_SOURCE_TO_TARGET, source_info, target_info)

            # target is older
            elif source_mt > target_mt:
                copy_index[relpath] = (Action.COPY_SOURCE_TO_TARGET, source_info, target_info)

            else:
                raise Exception(F"File {relpath} have same createdTime, but different file size: {source_size} != {target_size}! One of them might be corrupted!")

    return copy_index


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
                return "%7.3f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, 'Yi', suffix)

    print()
    print(Fore.GREEN + "SOURCE:" + Fore.RESET, args.source)
    print(Fore.YELLOW + "TARGET:" + Fore.RESET, args.target)
    print()

    print('{:17}'.format('Sync direction') + " | " + '{:41}'.format('Last modification time') + " | " + '{:23}'.format('File size') + " | Relative path:")
    print("-"*200)

    for relpath, (direction, source_info, target_info) in index.items():
        source_size, source_time = source_info[1:] if source_info is not None else (None, None)
        target_size, target_time = target_info[1:] if target_info is not None else (None, None)

        source_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(source_time))
        target_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(target_time))

        direction_str = Action.labels[direction]
        time_op = ">" if source_time > target_time else "<"
        if source_size > target_size:
            size_op = ">"
        elif source_size == target_size:
            size_op = "="
        else:
            size_op = "<"

        print(F"{direction_str:17s} | {source_time_str} {time_op} {target_time_str} | {sizeof_fmt(source_size)} {size_op} {sizeof_fmt(target_size)} | {relpath}")
    print()


def remove_file(filepath):
    if not args.dry_run:
        os.remove(filepath)
    else:
        print(F"DryRun: remove {filepath}")


def copy_file(src, dst):
    if not args.dry_run:
        shutil.copy2(src, dst)
    else:
        print(F"DryRun: copy {src} \n\t\t---> {dst}")


def execute_action(data):
    action, (source_path, _, _), (target_path, _, _) = data

    if action == Action.REMOVE_TARGET:
        remove_file(target_path)
    elif action == Action.COPY_SOURCE_TO_TARGET:
        copy_file(source_path, target_path)
    elif action == Action.COPY_TARGET_TO_SOURCE:
        copy_file(target_path, source_path)
    else:
        raise Exception(F"Unsupported action ID: {action}")

    return True


def main():
    with ThreadPoolExecutor(2) as pool:
        source_files, target_files = list(pool.map(list_files, [args.source, args.target]))

    with ThreadPoolExecutor() as pool:
        source_files_info = list(pool.map(get_file_info, source_files))
        target_files_info = list(pool.map(get_file_info, target_files))

    index = merge_trees(source_files_info, target_files_info)
    copy_index = get_sync_direction(index)

    if args.summary:
        print_summary(copy_index)

    print(F"Coping started at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

    workers = []
    with ThreadPoolExecutor(1 if args.dry_run else None) as pool:
        for data in copy_index.values():
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


if __name__ == '__main__':

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

    args = arg_parser.parse_args()
    args.exclude_folder_names = tuple(args.exclude_folder_names)
    args.exclude_file_ext = tuple(args.exclude_file_ext)

    if not os.path.isdir(args.source):
        raise Exception(f"Source folder path {args.source} does not exist!")
    if not os.path.isdir(args.target):
        raise Exception(f"Target folder path {args.target} does not exist!")
    if not args.one_direction_sync and args.delete_orphans:
        raise Exception("Flag --delete-orphans can be used only in combination with --one-direction-sync!")

    colorama.init()
    main()
