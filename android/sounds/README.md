# Sounds

`app/src/main/res/raw/liftoff.ogg`, the *Rocket* liftoff ring, is NASA's **SLS Test Fire** recording
from [Blast Through the Past](https://www.nasa.gov/historical-sounds/) (courtesy NASA; NASA audio isn't
subject to copyright in the US). It's the first 10.5 s, a "three, two, one" and ignition: the countdown
boosted, the roar faded out, a second of quiet so it repeats like an alarm. Rebuilt with:

```sh
curl -fLo sls.mp3 https://www.nasa.gov/wp-content/uploads/2015/01/663784main_SLS_Audio_D.mp3
ffmpeg -i sls.mp3 -af "atrim=0:10.5,volume=3.5:enable='lt(t,3.1)',afade=t=out:st=8.0:d=2.5,apad=pad_dur=1,aformat=channel_layouts=mono,alimiter=limit=0.7:level=false" \
  -ar 44100 -c:a libvorbis -q:a 4 app/src/main/res/raw/liftoff.ogg
```
