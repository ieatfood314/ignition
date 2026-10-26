# Sounds

`app/src/main/res/raw/liftoff.ogg`, the *Rocket* liftoff ring, is NASA's **SLS Test Fire** recording
from [Blast Through the Past](https://www.nasa.gov/historical-sounds/) (courtesy NASA; NASA audio isn't
subject to copyright in the US). It's 4.5 s from the moment of ignition (the voice counting down before it
is left out), eased in, faded out and turned down 12 dB so it's a nudge, not an alarm. Rebuilt with:

```sh
curl -fLo sls.mp3 https://www.nasa.gov/wp-content/uploads/2015/01/663784main_SLS_Audio_D.mp3
ffmpeg -i sls.mp3 -af "atrim=3.1:7.6,asetpts=PTS-STARTPTS,afade=t=in:d=0.3,afade=t=out:st=2.0:d=2.5,aformat=channel_layouts=mono,volume=-12dB,alimiter=limit=0.7:level=false" \
  -ar 44100 -c:a libvorbis -q:a 4 app/src/main/res/raw/liftoff.ogg
```
