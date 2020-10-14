#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

FILENAME="metaflow-ui.zip"
DEST=${1:-$DIR/ui}

UI_RELEASE_URL="https://github.com/Netflix/metaflow-ui/releases/download/${UI_VERSION}/metaflow-ui-${UI_VERSION}.zip"

if [ $UI_ENABLED = "1" ]
then
    echo "Download UI from $UI_RELEASE_URL and installing to $DEST"
    curl $UI_RELEASE_URL -o $FILENAME
    unzip -o $FILENAME -d $DEST
    rm $FILENAME
else
    echo "No UI enabled, skip download."
fi