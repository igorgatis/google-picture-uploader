#!/usr/bin/python

import Queue
import getpass
import hashlib
import itertools
import optparse
import os
import os.path
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
  def __init__(self, key):
    self.key = key
    self.path = None
    self.remote = None
    self.checksum_tag = None
    self.uploaded = False

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


class Album:
  def __init__(self, service, key):
    self.service = service
    self.key = key
    self.path = None
    self.remote = None
    self.photos = {}
    self.album_tags = frozenset(re.split('[\W\\/]+', key))
    self.photo_md5s = frozenset()

  def __repr__(self):
    return 'Album("%s", %s, %s)' % (
        self.key,
        ('"%s"' % self.path) if self.path else 'null',
        ('"%s"' % self.remote.title.text if self.remote else 'null'))

  def GetPhotosFromGoogle(self):
    print '  Getting list of photos from Google...',
    sys.stdout.flush()
    url = '/data/feed/api/user/default/albumid/%s?kind=photo' % (
        self.remote.gphoto_id.text)
    count = 0
    for photo in self.service.GetFeed(url).entry:
      key = photo.title.text
      if key not in self.photos:
        self.photos[key] = Photo(key)
      self.photos[key].remote = photo
      count += 1
    print 'found %d photos.' % count

  def GetImageExifDatetime(self, filepath):
    try:
      info = Image.open(filepath)._getexif()
      date_format = '%Y:%m:%d %H:%M:%S'
      epoch = datetime.datetime(1970, 1, 1)
      for code, value in info.items():
        if code in [306, 36867, 36868]:
          time = datetime.datetime.strptime(value, date_format)
          return (time - epoch).total_seconds()
    except:
      pass
    return None

  def ScanPhotosFromDisk(self):
    print '  Getting list of photos from disk...',
    sys.stdout.flush()
    count = 0
    for filename in os.listdir(self.path):
      filepath = os.path.join(self.path, filename)
      if IsPhoto(filepath):
        count += 1
        key, ext = os.path.splitext(filename)
        if key not in self.photos:
          self.photos[key] = Photo(key)
        photo = self.photos[key]
        photo.path = filepath
        photo.UpdateChecksum(self.album_tags)
    print 'found %d photos.' % count

  def _DeletePhoto(self, key):
    try:
      photo = self.photos[key]
      if photo.path == None:
        del self.photos[key]
        if photo.remote.GetEditLink():
          self.service.Delete(photo.remote)
    except:
      pass

  def DeletePhotos(self):
    print '  Deleting stale photos from Google...',
    sys.stdout.flush()
    for key in list(self.photos.keys()):
      g_workers.AddTask(self._DeletePhoto, key)
    g_workers.Wait()
    print 'done.'

  def FetchTags(self):
    tags = set()
    for entry in self.service.GetFeed(self.remote.GetTagsUri()).entry:
      tags.add(entry.title.text)
    return tags

  def _UploadPhoto(self, url, key, photo, callback):
    try:
      if photo.remote != None:
        if not g_options.force_update:
          if photo.checksum_tag in self.photo_md5s:
            photo.uploaded = True
            return
        if photo.remote.GetEditLink():
          self.service.Delete(photo.remote)
      tags = list(self.album_tags) + [photo.checksum_tag]
      self.service.InsertPhotoSimple(
          url, key, '', photo.path, keywords=tags)
      photo.uploaded = True
    except Exception as e:
      photo.uploaded = False
      print '  Failed to upload picture:', e
    finally:
      callback()

  def UploadPhotos(self):
    print '  Uploading photos to Google...   0%',
    sys.stdout.flush()
    url = '/data/feed/api/user/default/albumid/%s' %  (
        self.remote.gphoto_id.text)
    total = len(self.photos)
    counter = itertools.count(start=1)
    def callback():
      sys.stdout.write('\b\b\b\b%3d%%' % (100 * counter.next() / total))
      sys.stdout.flush()
    for key, photo in self.photos.iteritems():
      g_workers.AddTask(self._UploadPhoto, url, key, photo, callback)
    g_workers.Wait()
    print ' done.'

  def NeedsUpdate(self):
    if g_options.force_update:
      return True
    self.photo_md5s = frozenset([tag for tag in self.FetchTags()
                                 if tag.startswith('md5_')])
    if (len(self.photos) != int(self.remote.numphotos.text) or
        len(self.photos) != len(self.photo_md5s)):
      return True
    for key, photo in self.photos.iteritems():
      if photo.path and photo.checksum_tag not in self.photo_md5s:
        return True
    return False

  def SyncPhotos(self, service):
    if self.path == None or self.remote == None:
      return
    print 'Syncing [%s]' % (self.key)
    self.ScanPhotosFromDisk()
    if not self.NeedsUpdate():
      print '  Already up to date.'
      return
    self.GetPhotosFromGoogle()
    if g_options.delete_photos:
      self.DeletePhotos()
    self.UploadPhotos()


class GooglePictureUploader:
  def __init__(self):
    self.albums = {}
    self.service = gdata.photos.service.PhotosService()
    #self.service.source = 'GooglePictureUploader'
    self.service.email = g_options.email
    self.service.password = g_options.password
    self.service.ProgrammaticLogin()

  def GetAlbumsFromGoogle(self):
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

  def ScanAlbumsFromDisk(self):
    print 'Getting list of albums from disk...',
    sys.stdout.flush()
    count = 0
    for root, sub_folders, files in os.walk(g_options.root):
      for file in files:
        if IsPhoto(os.path.join(root, file)):
          key = TryStripPrefix(g_options.root, root)
          if key not in self.albums:
            self.albums[key] = Album(self.service, key)
          self.albums[key].path = root
          count += 1
          break
    print 'found %d albums.' % count

  def _DeleteAlbum(self, key):
    album = self.albums[key]
    if album.path == None and album.remote != None:
      del self.albums[key]
      if album.remote.GetEditLink():
        self.service.Delete(album.remote)

  def DeleteStaleAlbums(self):
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

  def SyncAlbums(self):
    print 'Syncing albums:'
    sys.stdout.flush()
    sorted_albums = []
    for key, album in self.albums.iteritems():
      if album.path != None and album.remote == None:
        sorted_albums.append((0, key, album))
      else:
        sorted_albums.append((1, key, album))
    sorted_albums.sort()
    for priority, key, album in sorted_albums:
      if album.path != None and album.remote == None:
        self._CreateAlbum(album)
      album.SyncPhotos(self)

  def Sync(self):
    self.ScanAlbumsFromDisk()
    self.GetAlbumsFromGoogle()
    if g_options.delete_albums:
      self.DeleteStaleAlbums()
    if self.albums:
      self.SyncAlbums()
    g_workers.Wait()


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
  parser.add_option('--parallelism', type='int', default=5,
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
