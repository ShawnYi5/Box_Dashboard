#!/bin/bash
BACK_PATH=''
DATE=`date +%Y%m%d`
echo $BACK_PATH
if [ -z "$BACK_PATH" ];then
     echo "back_path is empty"
else
     SAVE_PATH=${BACK_PATH}"$DATE"
     mkdir -p $SAVE_PATH
     chmod 700 $SAVE_PATH
     chown postgres:postgres $SAVE_PATH
     DIR=${SAVE_PATH}"/increment"
     (test -d $DIR || mkdir -p $DIR) && cp $1 $DIR/$2
     echo $SAVE_PATH
fi
exit 0
