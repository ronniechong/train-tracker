# train-tracker

Close-to-real-time Melbourne metro train tracker built on Victoria's open
GTFS-Realtime feeds: a polling/state service, JSON API + SSE stream, live map,
and an AI layer with clearly-labelled inferences.

> Work in progress. Architecture writeup lands when the project ships.

Design priorities: polite consumption of the upstream public API, security by
construction, deep observability, and data honesty (gaps recorded, staleness
displayed, inferences labelled).

Data: Metro Train GTFS-Realtime feeds via the Victorian Department of
Transport and Planning open data platform. Attribution and licence details in
the deployed site footer and here at release.
