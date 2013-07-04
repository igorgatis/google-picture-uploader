#!/bin/sh

pushd `dirname $0`/src
cp google-picture-uploader.py __main__.py
zip -r ../pack.zip * > /dev/null
echo "#!/usr/bin/env python" > ../google-picture-uploader
cat ../pack.zip >> ../google-picture-uploader
rm __main__.py ../pack.zip
chmod +x ../google-picture-uploader
popd

echo "New release: `dirname $0`/google-picture-uploader"
