LICENSED SOUNDTRACK FOR THE LAUNCH PAGE (/launch)
=================================================

Put here ONLY an audio file the company is licensed to host and play to
its staff, for example the intended track "Lilith" by Saint Avangeline once
a licence covering this use has been obtained from the rights holder(s).

Then set, as an administrator, and restart the tower:

    setx ATA_LAUNCH_AUDIO lilith.mp3 /M
    (optional) setx ATA_LAUNCH_AUDIO_TITLE "Lilith" /M
    (optional) setx ATA_LAUNCH_AUDIO_ARTIST "Saint Avangeline" /M

Instead of a file, ATA_LAUNCH_AUDIO may be an https:// address of a licensed
stream whose terms allow this use.

Accepted: .mp3 .m4a .ogg .oga .opus .webm .wav .flac. Nothing in this folder
is committed to Git or packed into the release ZIP: the licence is for this
installation, not for redistribution. With no file configured, or a file
that cannot be played, the player shows "Soundtrack unavailable" and the
page works normally. Nothing set means no music: a file left in this folder
is not played unless ATA_LAUNCH_AUDIO names it.

Nothing about the music shows on the page.
The music loops and starts by itself where the browser allows it; Chrome and
Edge only allow that after the visitor has clicked or pressed a key, so
otherwise it starts on the first click or key press anywhere ("Click
anywhere for sound"). To let it start with no click at all, IT can add the
site to the browser policy AutoplayAllowlist (Edge and Chrome, Group
Policy), e.g. [*.]mantrac.com. The corner switch stops it; leaving the page
stops it.
