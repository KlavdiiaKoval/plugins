# plugins

Claude Code plugins for engineering teams.

| Plugin | What it does |
| --- | --- |
| [pr0](plugins/pr0) | Shapes a big feature as one small PR of types, interfaces and stubs, plus a design doc, before anyone writes the logic. |
| [gate](plugins/gate) | Stops Claude from muting checks, force pushing or reading secrets, and from saying done while build, lint or tests fail. |

## Install

Add this marketplace once:

```
/plugin marketplace add KlavdiiaKoval/plugins
```

Then install a plugin:

```
/plugin install pr0@klavdiiakoval
/plugin install gate@klavdiiakoval
```

## License

MIT
