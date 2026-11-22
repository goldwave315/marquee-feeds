# marquee-feeds

Every three hours this repo reads public show calendars for a set of Portland, Oregon music venues and writes them into one list.

| File | What it is |
|---|---|
| `data/events.json` | Every upcoming show it can see, with the date it was first seen |
| `data/new.json` | Shows first seen in the last 14 days, newest first |
| `data/new.xml` | The same new shows as an RSS feed |
| `data/seen.json` | When each show was first seen, so a show that briefly drops off a page isn't reported again |

Venues: Mississippi Studios, Polaris Hall, Revolution Hall, Show Bar, Aladdin Theater, Star Theater, Jack London Revue, The Get Down, Wonder Ballroom, Holocene, Pioneer Courthouse Square and McMenamins Edgefield. It also checks whether the Doug Fir Lounge website is back up.

Each venue is read from its own feed or calendar page. If one venue fails, its previous list is kept and the failure shows up under `sources` in `events.json`.

Raw data: `https://raw.githubusercontent.com/goldwave315/marquee-feeds/main/data/new.json`
