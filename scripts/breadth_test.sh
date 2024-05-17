#!/usr/bin/bash
echo "Create $1 dirs"
cd ~/test/watched
mkdir many
date
for ((i=1; i<=$1; i++))
do
    echo many/m$i
    mkdir many/m$i
done
date
echo "Sleep $2 secs"
sleep $2
rm -r many