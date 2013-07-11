#!/usr/bin/python

import Queue
import getpass
import hashlib
import itertools
import optparse
import os
import os.path
import progressbar
import re
import sys
import threading
import time

import gdata.photos.service

g_options = None
g_workers = None

class ThreadPool:
  def __init__(self, num_threads):
    self.tasks = Queue.Queue(num_threads)
    def run(index, tasks):
      while True:
        func, args, kargs = tasks.get()
        try:
          func(*args, **kargs)
        except (KeyboardInterrupt, SystemExit):
          return
        except Exception as e:
          print e
        finally:
          tasks.task_done()
    for i in range(num_threads):
      thread = threading.Thread(target=run, args=(i, self.tasks))
      thread.daemon = True
      thread.start()

  def AddTask(self, func, *args, **kargs):
    self.tasks.put((func, args, kargs))

  def Wait(self):
    self.tasks.join()


def IsPhoto(file):
  if not os.path.isfile(file):
    return False
  _, ext = os.path.splitext(file)
  return ext and ext.lower() in ['.jpg', '.png', '.gif']


def TryStripPrefix(prefix, text):
  if text.startswith(prefix):
    return text[len(prefix):]
  return text


class Photo:
  ERROR = 'error'
  NONE = 'none'
  SKIPPED = 'skip'
  UPLOADED = 'upload'
  ALL_STATUS = [ERROR, NONE, SKIPPED, UPLOADED]

  def __init__(self, key):
    self.key = key
    self.path = None
    self.remote = None
    self.checksum_tag = None
    self.status = Photo.NONE

  def __repr__(self):
    return 'Photo("%s", %s, %s)' % (
        self.key,
        ('"%s"' % self.path) if self.path else 'null',
        ('"%s"' % self.remote.title.text if self.remote else 'null'))

  def UpdateChecksum(self, tags):
    md5 = hashlib.md5()
    md5.update(self.key)
    md5.update(tags.__str__())
    stream = open(self.path, 'rb')
    while True:
      data = stream.read(128)
      if not data:
        break
      md5.update(data)
    stream.close()
    self.checksum_tag = 'md5_%s' % md5.hexdigest()


class Counter:
  def __init__(self):
    self._counter = itertools.count()
    self._value = 0

  def inc(self):
    self._value = self._counter.next()
    return self._value

  def value(self):
    return self._value


class Album:
  def __init__(self, service, key):
    self.service = service
    self.key = key
    self.path = None
    self.num_local_photos = 0
    self.remote = None
    self.photos = None
    self.photo_md5s = None
    self.needs_update = True
    self.album_tags = frozenset(re.split('[\W\\/]+', key))

  def __repr__(self):
    return 'Album("%s", %s, %s)' % (
        self.key,
        ('"%s"' % self.path) if self.path else 'null',
        ('"%s"' % self.remote.title.text if self.remote else 'null'))

  def _ScanPhotosFromDisk(self):
    if self.photos == None:
      self.photos = {}
      if self.path == None:
        return
      for filename in os.listdir(self.path):
        filepath = os.path.join(self.path, filename)
        if IsPhoto(filepath):
          key, ext = os.path.splitext(filename)
          if key not in self.photos:
            self.photos[key] = Photo(key)
          photo = self.photos[key]
          photo.path = filepath
          photo.UpdateChecksum(self.album_tags)

  def _GetPhotoMd5s(self):
    if self.photo_md5s == None:
      tags = set()
      for entry in self.service.GetFeed(self.remote.GetTagsUri()).entry:
        if entry.title and entry.title.text.startswith('md5_'):
          tags.add(entry.title.text)
      self.photo_md5s = frozenset(tags)
    return self.photo_md5s

  def CheckNeedUpdate(self):
    if self.remote == None:
      self.needs_update = True
      return
    self._ScanPhotosFromDisk()
    md5s = self._GetPhotoMd5s()
    count = 0
    for key, photo in self.photos.iteritems():
      if photo.path:
        count += 1
        if photo.checksum_tag not in md5s:
          self.needs_update = True
          return
    self.needs_update = count != len(md5s)

  def _GetPhotosFromGoogle(self):
    url = '/data/feed/api/user/default/albumid/%s?kind=photo' % (
        self.remote.gphoto_id.text)
    for photo in self.service.GetFeed(url).entry:
      key = photo.title.text
      if key not in self.photos:
        self.photos[key] = Photo(key)
      self.photos[key].remote = photo

  def _DeletePhoto(self, key):
    try:
      photo = self.photos[key]
      if photo.path == None:
        del self.photos[key]
        if photo.remote.GetEditLink():
          self.service.Delete(photo.remote)
    except:
      pass

  def _DeletePhotos(self):
    for key in list(self.photos.keys()):
      g_workers.AddTask(self._DeletePhoto, key)
    g_workers.Wait()

  def _FetchPhotoTags(self, photo):
    tags = set()
    for entry in self.service.GetFeed(photo.remote.GetTagsUri()).entry:
      tags.add(entry.title.text)
    return tags

  def _UploadPhoto(self, url, key, photo, callback):
    try:
      if photo.remote != None:
        if not g_options.force_update:
          if photo.checksum_tag in self._GetPhotoMd5s():
            photo.status = Photo.SKIPPED
            return
          photo_tags = self._FetchPhotoTags(photo)
          if photo.checksum_tag in photo_tags:
            photo.status = Photo.SKIPPED
            return
        if photo.remote.GetEditLink():
          self.service.Delete(photo.remote)
      tags = sorted(list(self.album_tags) + [photo.checksum_tag])
      self.service.InsertPhotoSimple(
          url, key, '', photo.path, keywords=tags)
      photo.status = Photo.UPLOADED
    except Exception as e:
      photo.status = Photo.ERROR
    finally:
      callback(photo)

  class CountersWidget(progressbar.Widget):
    def __init__(self, counters):
      progressbar.Widget.__init__(self)
      self.counters = counters
    def update(self, pbar):
      pairs = ['%3d:%s' % (counter.value(), name)
               for name, counter in self.counters.iteritems()]
      return ' '.join(pairs)

  def _PrintStatusLine(self, pbar, counters):
    total = sum([counter.value() for counter in counters.values()])
    pbar.update(total)

  def _UploadPhotos(self):
    url = '/data/feed/api/user/default/albumid/%s' %  (
        self.remote.gphoto_id.text)
    counters = dict([(name, Counter()) for name in Photo.ALL_STATUS])
    widgets = ['Syncing [%s]' % self.key,
               ' ', progressbar.Bar(left='[', right=']'),
               ' ', self.CountersWidget(counters),
               '|', progressbar.SimpleProgress(),
               '|', progressbar.Percentage(),
               '|', progressbar.AdaptiveETA()]
    pbar = progressbar.ProgressBar(widgets=widgets, maxval=len(self.photos))
    pbar.start()
    def callback(photo):
      counters[photo.status].inc()
      self._PrintStatusLine(pbar, counters)
    for key, photo in self.photos.iteritems():
      g_workers.AddTask(self._UploadPhoto, url, key, photo, callback)
    g_workers.Wait()
    pbar.finish()

  def SyncPhotos(self, service):
    if self.path == None or self.remote == None:
      return
    self._ScanPhotosFromDisk()
    self._GetPhotosFromGoogle()
    if g_options.delete_photos:
      self._DeletePhotos()
    self._UploadPhotos()


class GooglePictureUploader:
  def __init__(self):
    self.albums = {}
    self.service = gdata.photos.service.PhotosService()
    #self.service.source = 'GooglePictureUploader'
    self.service.email = g_options.email
    self.service.password = g_options.password
    self.service.ProgrammaticLogin()

  def _GetAlbumsFromGoogle(self):
    print 'Getting list of albums from Google...',
    sys.stdout.flush()
    count = 0
    for album in self.service.GetUserFeed(kind='album').entry:
      key = album.title.text
      if key not in self.albums:
        self.albums[key] = Album(self.service, key)
      count += 1
      self.albums[key].remote = album
    print 'found %d albums.' % count

  def _ScanAlbumsFromDisk(self):
    print 'Getting list of albums from disk...',
    sys.stdout.flush()
    album_count = 0
    for root, sub_folders, files in os.walk(g_options.root):
      photo_count = 0
      for file in files:
        if IsPhoto(os.path.join(root, file)):
          photo_count += 1
      if photo_count > 0:
        album_count += 1
        key = TryStripPrefix(g_options.root, root)
        if key not in self.albums:
          self.albums[key] = Album(self.service, key)
        self.albums[key].path = root
        self.albums[key].num_local_photos = photo_count
    print 'found %d albums.' % album_count

  def _DeleteAlbum(self, key):
    album = self.albums[key]
    if album.path == None and album.remote != None:
      del self.albums[key]
      if album.remote.GetEditLink():
        self.service.Delete(album.remote)

  def _DeleteStaleAlbums(self):
    print 'Deleting stale albums from Google...',
    sys.stdout.flush()
    for key in list(self.albums.keys()):
      g_workers.AddTask(self._DeleteAlbum, key)
    g_workers.Wait()
    print 'done.'

  def _CreateAlbum(self, album):
    entry = self.service.InsertAlbum(
        album.key,  # title
        '',         # summary
        access=g_options.album_access,
        commenting_enabled='false')
    album.remote = self.service.GetFeed(entry.GetFeedLink().href)

  def _CheckAlbumNeedUpdate(self, album, callback):
    try:
      album.CheckNeedUpdate()
    except Exception as e:
      print e
    finally:
      callback(album)

  def _SyncAlbums(self):
    if not g_options.force_update:
      counter = itertools.count()
      widgets = ['Searching for album changes:',
                 ' ', progressbar.Bar(left='[', right=']'),
                 '|', progressbar.SimpleProgress(),
                 '|', progressbar.Percentage(),
                 '|', progressbar.AdaptiveETA()]
      pbar = progressbar.ProgressBar(widgets=widgets, maxval=len(self.albums))
      pbar.start()
      def callback(album):
        pbar.update(counter.next())
      for key, album in self.albums.iteritems():
        g_workers.AddTask(self._CheckAlbumNeedUpdate, album, callback)
      g_workers.Wait()
      pbar.finish()
    print 'Syncing albums...'
    for key, album in self.albums.iteritems():
      if album.path != None and album.remote == None:
        self._CreateAlbum(album)
      if album.needs_update:
        album.SyncPhotos(self)
    print 'done.'

  def Sync(self):
    self._ScanAlbumsFromDisk()
    self._GetAlbumsFromGoogle()
    if g_options.delete_albums:
      self._DeleteStaleAlbums()
    if self.albums:
      self._SyncAlbums()


def main():
  global g_options
  parser = optparse.OptionParser()
  parser.add_option('-r', '--root', dest='root', default='.',
                    help=('Base path of photos to upload.'
                          ' Albums will be named relative to this path.'))
  parser.add_option('-e', '--email', dest='email',
                    help='Google account (e.g. joe@gmail.com).')
  parser.add_option('-p', '--password', dest='password',
                    help='Google account password')
  parser.add_option('--new_albums_visibility', dest='album_access',
                    default='private',
                    help=('Visibility of newly create albums: private|public".'
                          ' Does not change visibility of existing albums.'))
  parser.add_option('--force_update', action='store_true', default=False,
                    help=('Whether or not verify photo metadata individually'
                          ' (slow and bandwidth consuming).'))
  parser.add_option('--delete_albums', action='store_true', default=False,
                    help=('Whether or not keep albums which are not present'
                          ' locally.'))
  parser.add_option('--delete_photos', action='store_true', default=False,
                    help=('Whether or not keep photo which are not present'
                          ' locally.'))
  parser.add_option('--parallelism', type='int', default=3,
                    help='Number of parallel HTTP requests.')
  g_options, _ = parser.parse_args()

  if g_options.album_access not in ['private', 'public']:
    parser.error('Unknown visibility option: %s' % g_options.album_access)
    return

  g_options.root = os.path.expanduser(g_options.root)
  g_options.root = os.path.abspath(g_options.root) + os.sep
  print 'Syncing %s' % g_options.root
  if not g_options.email:
    g_options.email = raw_input('Google account: ')
  if not g_options.password:
    g_options.password = getpass.getpass()

  global g_workers
  g_workers = ThreadPool(g_options.parallelism)
  GooglePictureUploader().Sync()


if __name__ == "__main__":
  main()
