#!/usr/bin/bash
echo "Create $1 dirs"
cd ~/test/watched
mkdir many
for ((i=1; i<=$i; i++))
do
    mkdir many/m$i
done
echo "Sleep $2 secs"
sleep $2
rm -r many