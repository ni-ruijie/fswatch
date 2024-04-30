delay=0.1
cd ..
sleep $delay
rm -r test
sleep $delay
mkdir test
sleep $delay
touch test/foo.py
sleep $delay
rm test/foo.py
sleep $delay
cd test
