import os
import pickle
import requests
import s3fs
import shutil
from datetime import datetime
from dateutil import parser as dup
from urllib.parse import urlparse
from urllib.request import urlopen

import logging
log = logging.getLogger(__name__)

logging.basicConfig(level="INFO")

class CacheEntry():
    def __init__(self, url):
        self.url = None
        self.dest = None

    @property
    def is_local(self):
        return os.path.exists(self.dest)



class S3CacheEntry(CacheEntry):
    def __init__(self, url):
        self.url = url
        self.fs = s3fs.S3FileSystem(anon=True)
        self.info = self.fs.info(self.url)

    def fetch(self, dest):
        log.info(f"Retrieving {self.url} to {dest} ...")
        self.fs.get(self.url, dest)
        log.info(f"Retrieved {dest} ({os.stat(dest).st_size} bytes)")
        self.dest = dest

    @property
    def checksum(self):
        return self.info["ETag"]

    def __eq__(self, rhs):
        if not isinstance(rhs, self.__class__):
            raise TypeError(f"Invalid comparison between types of "
                             "{self.__class__.__qualname__} and {rhs.__class__.__qualname__}")

        return self.checksum == rhs.checksum



class HttpCacheEntry(CacheEntry):
    def __init__(self, url):
        self.url = url
        self.head()

    def fetch(self, dest):
        log.info(f"Retrieving {self.url} to {dest} ...")

        path = os.path.dirname(dest)
        if not os.path.exists(path):
            os.makedirs(path)

        with urlopen(self.url) as resp, open(dest, "wb") as fp:
            shutil.copyfileobj(resp, fp)

        log.info(f"Retrieved {dest} ({os.stat(dest).st_size} bytes)")
        self.dest = dest

    def head(self):
        resp = requests.head(self.url)
        if resp.status_code != 200:
            u = urlparse(self.url)
            log.info(f"Server at {u.netloc} does not support HTTP HEAD")
            return

        if lastmod := resp.headers.get("Last-Modified"):
            try:
                self.last_modified = dup.parse(lastmod)
            except dup.ParserError:
                if contlen := resp.headers.get("Content-Length"):
                    try:
                        self.content_length = int(contlen)
                    except:
                        pass

    def __eq__(self, rhs):
        if not isinstance(rhs, self.__class__):
            raise TypeError(f"Invalid comparison between types of "
                             "{self.__class__.__qualname__} and {rhs.__class__.__qualname__}")

        cmp_attrs = ("last_modified", "content_length")
        for attr in cmp_attrs:
            if hasattr(self, attr) and hasattr(rhs, attr):
                return getattr(self, attr) == getattr(rhs, attr)

        return False


    def hash(self):
        pass


class FsCacheEntry(CacheEntry):
    def __init__(self, url):
        pass


class FileCache():

    scheme_map = {
        "s3": S3CacheEntry,
        "http": HttpCacheEntry,
        "https": HttpCacheEntry,
        "": FsCacheEntry
    }

    def __init__(self, cache_root):
        self.cache_root = cache_root
        self.state_file = os.path.join(cache_root, ".cache_state")
        self.buf = dict()

    def __len__(self):
        return len(self.buf)

    def __contains__(self, key):
        return key in self.buf

    def add(self, url):
        entry = self.__class__.lookup_type(url)(url)
        dest = self.url_to_local_path(url)
        print(f"{dest=}")
        entry.fetch(dest)
        self.buf[url] = entry

        return entry

    @classmethod
    def lookup_type(cls, path):
        u = urlparse(path)
        scheme_type = cls.scheme_map.get(u.scheme)
        if not scheme_type:
            raise ValueError(f"[{cls.__qualname__}] Unsupported scheme: '{u.scheme}'")

        return scheme_type

    def url_to_local_path(self, url):
        u = urlparse(url)
        filepath = f"{u.netloc}{u.path}"
        return os.path.join(self.cache_root, filepath)

    @property
    def url_is_current(self, url):
        if url not in self:
            return False

    def purge_local_file(self, filename):
        """
          This prevents directory traversal by:
            - relative path components
            - ~user path components
            - $ENV_VAR components
            - paths containing hard or symbolic links

          A valid target file must be all of:
            - a real absolute filesystem path
            - a subtree of the cache_root
            - not a directory
            - not a link
            - not a device file or pipe
            - owned by the Hydra user

          In addition, the cache_root may not be:
            - undefined
            - the root filesystem
            - the root of any mount point

          ValueError is raised if any of these conditions
          are not met.
        """
        real_cache_root = os.path.realpath(self.cache_root)
        if not self.cache_root or real_cache_root == '/' or os.path.ismount(real_cache_root):
            raise ValueError(f"Invalid cache_root configuration value '{self.cache_root}'")

        expanded = os.path.expandvars(filename)
        if expanded != filename:
            raise ValueError(f"Invalid path '{filename}': Arguments may not contain variables")
        target = os.path.realpath(expanded)
        if os.path.commonprefix([target, self.cache_root]) != self.cache_root:
            raise ValueError(f"Invalid path '{filename}': Only cache files may be purged")

        if not os.path.exists(target):
            raise ValueError(f"Invalid path '{filename}': File does not exist")

        # Tests for directories, device files and pipes, and existence again
        if not os.path.isfile(target):
            raise ValueError(f"Invalid path '{filename}': Only regular files may be purged")

        if os.getuid() != os.stat(target).st_uid:
            raise ValueError(f"Invalid path '{filename}': File is not owned by "
                             f"user {os.getlogin()} ({os.getuid()})")
        try:
            os.unlink(target)
        except OSError as oe:
            raise ValueError(f"Invalid path '{filename}': Unable to purge file") from oe

        return target

    def purge_all(self):
        pass

    def purge_entry(self, url):
        try:
            entry = self.buf[url]
            purged = self.purge_local_file(entry.dest)
            log.info(f"Purged local file at {purged} for {url}")
            del self.buf[url]
        except KeyError:
            raise ValueError(f"Cache does not contain entry for {url}") from None

    def add_file_as_entry(self, path, url):
        pass

    def get(self, url):
        new_entry = self.__class__.lookup_type(url)(url)
        if url in self:
            if self.buf[url] != new_entry:
                entry = self.add(url)
            else:
                entry = self.buf[url]
        else:
            entry = self.add(url)

        return entry.dest


    def save_state(self, state_file=None):
        state_file = state_file if state_file else self.state_file
        with open(state_file, 'wb') as fp:
            try:
                log.info(f"Saving cache state to {state_file}...")
                pickle.dump(self.buf, fp, protocol=pickle.HIGHEST_PROTOCOL)
            except pickle.PicklingError as e:
                log.error(f"Error saving state to {state_file}: {e}")

    def load_state(self, state_file=None):
        state_file = state_file if state_file else self.state_file
        with open(state_file, 'rb') as fp:
            try:
                log.info(f"Loading cache state from {state_file}...")
                self.buf = pickle.load(fp)
            except (pickle.UnpicklingError, EOFError) as e:
                log.error(f"Error loading state from {state_file}: {e}")


if __name__ == "__main__":
    s3_url = "s3://modelers-data-bucket/eapp/single/ETH_flow_sim.h5"
    http_url = "https://hwi-test.s3.amazonaws.com/base/static/js/third-party/sprintf.min.js"
    fc = FileCache("/tmp/cache")
    fc.load_state()
    print(f"{s3_url in fc=}")
    print(f"{http_url in fc=}")
    s3_cached = fc.get(s3_url)
    http_cached = fc.get(http_url)
    print(s3_cached)
    print(http_cached)
    #fc.purge_entry(s3_url)
    #print(f"{s3_url in fc=}")
    #s3_cached = fc.get(s3_url)
    fc.save_state()
    #fc.purge_entry("nonexistent.txt")
    #fc.purge_entry("~paul/unsafe.txt")
