delay=0.1
mkdir aa
sleep $delay
mkdir -p bb/ccc
sleep $delay
touch aa/foo.py
sleep $delay
touch bb/ccc/bar.py
sleep $delay
rm -r aa
sleep $delay
rm -r bb
