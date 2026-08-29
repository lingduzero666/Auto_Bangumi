# Calendar View

Since v3.2, AB includes a calendar view that shows your subscribed anime organized by broadcast day.

![Calendar](/image/feature/calendar.png)

## Features

### Weekly Schedule

The calendar displays anime organized by their broadcast weekday (Monday through Sunday), plus an "Unknown" column for anime without broadcast schedule data.

### Broadcast Schedule Sources

AB has two sources for the air weekday. Pick which one is asked first under **Settings → Parser Setting → Air weekday source**:

- **Bangumi.tv** (default): matches your anime against Bangumi.tv's broadcast schedule by title.
- **Parsers**: follows each subscription's own parser type — `mikan` and `mix` read the air day written on the Mikan bangumi page, `tmdb` derives it from TMDB air dates, other parser types have no parser source.

Either way, the other source is used as a fallback for anime the preferred one cannot resolve, so both settings shrink the "Unknown" column. Prefer parsers when bgm.tv is unreachable from your network.

A weekday you set by hand (dragging an anime on the calendar) always wins and is never overwritten by a refresh.

Click the **Refresh schedule** button to update the broadcast data. With parsers first this fetches per anime, so it takes longer than the single Bangumi.tv request.

### Grouped Display

Since v3.2, anime with multiple download rules are grouped together:

- Same anime appears once, even with multiple subtitle group rules
- Click on a grouped anime to see all available rules
- Select a specific rule to edit

This keeps the calendar clean while still providing access to all your rules.

## Navigation

Click on any anime poster in the calendar to:
- View anime details
- Edit download rules
- Access archive/disable options

## Tips

::: tip
An anime in the "Unknown" column means neither source could resolve its air weekday. Try the other "Air weekday source" and refresh again, or just drag the anime onto the right day to set it by hand.
:::
