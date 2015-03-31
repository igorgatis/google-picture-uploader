===What does it do?===

This python script syncs albums in picasa with a given folder tree. It is meant to be a simple rsync-like mechanism where on-line albums are the weak end point.

You provide a root path and this script creates albums with "folder/like/names" for sub-folder under the given root. All albums are private and don't accept comments.

This is a great way to backup your pictures and made them automatically available in your Android phone.

===How to use?===

Upload all pictures under `~/Pictures`:
{{{
google-picture-uploader --email=joe@gmail.com --root=~/Pictures
}}}
This will create an album `foo` for pictures under `~/Pictures/foo`.

Output example:
{{{
Getting list of albums from disk... found 426 albums.
Getting list of albums from Google... found 427 albums.
Deleting stale albums from Google... done.
Searching for album changes: [#############################]|426 of 426|100%|Time: 0:27:27
Syncing albums...
Syncing [Rio de Janeiro] [#################] 20:skip 15:upload|35 of 35|100%|Time: 0:00:29
Syncing [Las Vegas] [############          ] 10:skip 30:upload|40 of 80| 50%|ETA:  0:00:23
}}}

Features (from --help):
{{{
  -r ROOT, --root=ROOT  Base path of photos to upload. Albums will be named
                        relative to this path.
  -e EMAIL, --email=EMAIL
                        Google account (e.g. joe@gmail.com).
  -p PASSWORD, --password=PASSWORD
                        Google account password. If missing, password will be
                        asked at prompt time.
  --new_albums_visibility=ALBUM_ACCESS
                        Visibility of newly create albums: private|public.
                        Does not change visibility of existing albums.
  --quick_check         Only checks number of photos per album in order to
                        tell when the album needs to be updated.
  --force_update        Whether or not verify photo metadata individually
                        (slow and bandwidth consuming). Use this on your first
                        run and then start using --quick_check.
  --delete_albums       Whether or not keep albums which are not present
                        locally. USE CAREFULLY!
  --delete_photos       Whether or not keep photo which are not present
                        locally. Use carefully.
  --parallelism=PARALLELISM
                        Number of parallel HTTP requests.
}}}

===Releases===

<a href="https://drive.google.com/folderview?id=0Bx6DjGBhfk5mN1JZSGw1T1cybjA&usp=sharing">Google Drive</a>
