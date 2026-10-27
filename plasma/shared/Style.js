.pragma library
// The look shared by every ignition widget: Nothing OS's dot-matrix and mono typefaces (the same ones the
// Android widgets use), the theme's text colour for everything, and one sparse accent: Nothing's red unless
// the widget's settings pick another.

const families = Qt.fontFamilies();

function pick(names, fallback) {
    for (const n of names) {
        if (families.indexOf(n) >= 0)
            return n;
    }
    return fallback;
}

const dot = pick(["Ndot 57", "Ndot 55", "Doto"], "monospace");
const dotCaps = pick(["Ndot57Caps", "Ndot55Caps", "Ndot 57", "Doto"], "monospace");
const mono = pick(["Lettera Mono LL", "Space Mono", "JetBrains Mono", "Hack", "DejaVu Sans Mono"], "monospace");

const red = "#D71921";

// The accent a widget's settings ask for: `system` is the accent colour from System Settings.
function accent(config, system) {
    return config.accent === "system" ? system : config.accent === "custom" ? config.accentColor : red;
}
const secondary = 0.55;
const dim = 0.18;

function alpha(c, a) {
    return Qt.rgba(c.r, c.g, c.b, a);
}
