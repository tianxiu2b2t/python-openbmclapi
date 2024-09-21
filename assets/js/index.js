class Utils {
    static uuid(len, radix) {
        var chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'.split('');
        var uuid = [], i;
        radix = radix || chars.length;
     
        if (len) {
            for (i = 0; i < len; i++) uuid[i] = chars[0 | Math.random()*radix];
        } else {
            var r;
            uuid[8] = uuid[13] = uuid[18] = uuid[23] = '-';
            uuid[14] = '4';
            for (i = 0; i < 36; i++) {
                if (!uuid[i]) {
                    r = 0 | Math.random()*16;
                    uuid[i] = chars[(i == 19) ? (r & 0x3) | 0x8 : r];
                }
            }
        }
        return uuid.join('').toLocaleLowerCase();
    }
    static isDOM(o) {
        return (
            typeof HTMLElement === "object" ? o instanceof HTMLElement : //DOM2
            o && typeof o === "object" && o !== null && o.nodeType === 1 && typeof o.nodeName==="string"
        );
    }
}
class Socket {
    constructor(baseurl) {
        this._ws = null;
        this._wsReconnectTask = null;
        this._clientId = Utils.uuid();
        this._baseurl = baseurl;
        this._url = this._baseurl + "?id=" + this._clientId
        this._keepaliveTask = null;
        this._echoCallbacks = {}
        this._wsConnect()
        window.addEventListener("beforeunload", () => {
            this.send("disconnect")
            this._ws?.close()
        })
        setInterval(() => this._keepalive(), 5000);
        this._keepalive();
    }
    async _keepalive() {
        console.log(await this.send("keepalive", {
            "timestamp": new Date()
        }))
    }
    async _sendByXHR(event, data) {
        var xhr = new XMLHttpRequest();
        var echo_id = Utils.uuid();
        xhr.addEventListener("readystatechange", (event) => {
            if (event.target.readyState == XMLHttpRequest.DONE) {
                this._dispatchData(JSON.parse(xhr.response)); 
            }
        })
        xhr.open("POST", this._url);
        return this._setEchoCallback(echo_id, () => {
            xhr.send(JSON.stringify({
                "event": event,
                "data": data,
                echo_id
            }));
            setTimeout(() => {
                xhr.abort();
            }, 10000)
        });
    }
    _dispatchData(responses) {
        responses.forEach(response => {
            const { echo_id, event, data } = response;
            console.log(echo_id, event)
            if (echo_id == null) { // global dispatch event

            } else if (echo_id in this._echoCallbacks) {
                var { resolve, reject, timer } = this._echoCallbacks[echo_id];
                delete this._echoCallbacks[echo_id];
                clearTimeout(timer);
                resolve(data);
            }
        });
    }
    _wsConnect() {
        clearTimeout(this._wsReconnectTask)
        this._ws?.close()
        this._ws = new WebSocket(
            this._url
        )
        this._ws.addEventListener("close", () => {
            console.warn("The websocket has disconnected. After 5s to reconnect.")
            setTimeout(() => {
                this._wsConnect()
            }, 5000)
        })
        this._ws.addEventListener("message", (event) => {
            var raw_data = JSON.parse(event.data);
            this._dispatchData(raw_data)
        })
    }
    _sendByWs(event, data) {
        if (this._ws?.readyState != WebSocket.OPEN) return;
        var echo_id = Utils.uuid();
        return this._setEchoCallback(echo_id, () => {
            this._ws.send(JSON.stringify(
                {
                    "event": event,
                    "data": data,
                    echo_id
                }
            ))
        });
    }
    _setEchoCallback(id, executor) {
        return new Promise((resolve, reject) => {
            this._echoCallbacks[id] = { resolve, reject, timer: setTimeout(() => {
                reject("Timeout Error.")
            }, 10000)};
            executor();
        })
    }
    async send(event, data) {
        var handler = this._ws?.readyState == WebSocket.OPEN ? this._sendByWs : this._sendByXHR
        return handler.bind(this)(event, data);
    }
    
}
class I18NManager {
    constructor() {
        this._i18n = {}
        this._lang = "zh_CN"
    }
    addLangage(lang, key, value) {
        if (!(lang in this._i18n)) {
            this._i18n[lang] = {}
        }
        this._i18n[lang][key] = value;
    }
    addLanguageTable(lang, table) {
        table.entries().forEach(([key, value]) => {
            this.addLangage(lang, key, value)
        })
    }
    t(key, params) {
        if (!(this._lang in this._i18n)) {
            return key;
        }
        var value = this._i18n[this._lang][key];
        if (value == null) {
            return key;
        }
        params.entries().forEach(([key, value]) => {
            value = value.replace(`%${key}%`, value);
        })
        return value;
    }
    setLang(lang) {
        this._lang = lang;
        window.dispatchEvent(new CustomEvent("langChange", { detail: lang }))
    }
}
class ElementManager {
    constructor() {
        this._elements = []
        window.addEventListener("langChange", (event) => {
            this._elements.forEach(element => element._render_i18n())
        })
    }
    add(element) {
        this._elements.push(element);
    }
}
class Element {
    constructor(object) {
        if (typeof object == "string") {
            this._base = document.createElement(object);
        } else if (Utils.isDOM(object)) {
            this._base = object;
        } else {
            console.log(object)
        }
        this._i18n_key = null;
        this._i18n_params = {};
        this._children = []
        $ElementManager.add(this);
    }
    get origin() {
        return this._base;
    }
    html(html) {
        this._base.innerHTML = html;
        return this;
    }
    text(text) {
        this._base.innerText = text;
        return this;
    }
    i18n(key) {
        this._i18n_key = key;
        this._render_i18n();
        return this;
    }
    t18n(params) {
        this._i18n_params = params || {};
        this._render_i18n();
        return this;
    }
    _render_i18n() {
        if (this._i18n_key == null) {
            return;
        }
        this.text($i18n.t(this._i18n_key))
    }
    append(...elements) {
        elements.forEach(element => {
            if (Utils.isDOM(element)) {
                element = new Element(element);
            }
            this._children.push(element);
            this._base.appendChild(element.origin);
        })
        return this
    }
    classes(...classes) {
        this._base.classList.add(...classes);
        return this;
    }
    removeClasses(...classes) {
        this._base.classList.remove(...classes);
        return this;
    }
    style(key, value) {
        this._base.style[key] = value;
        return this;
    }
    on(event, handler) {
        this._base.addEventListener(event, handler);
        return this;
    }
    get children() {
        return this._children;
    }
    get length() {
        return this._children.length;
    }
    removeChild(object) {
        // first number
        // second dom
        // last element
        if (typeof object == "number") {
            this._children.splice(object, 1);
        } else if (Utils.isDOM(object)) {
            this._children.splice(this._children.indexOf(new Element(object)), 1);
        } else {
            this._children.splice(this._children.indexOf(object), 1);
        }
        return this
    }
    get firstChild() {
        return this._children[0];
    }
    get lastChild() {
        return this._children[this._children.length - 1];
    }
    remove() {
        this._children.forEach(child => child.remove());
        this._base.remove();
    }
    appendBefore(element) {
        this._children.unshift(element);
        this._base.insertBefore(element.origin, this._base.firstChild);
        return this
    }
    attributes(attributes) {
        Object.entries(attributes).forEach(([key, value]) => {
            this._base.setAttribute(key, value);
        })
        return this;
    }
}
class Configuration {
    constructor() {
        // use local storage
    }
    get(key, _def) {
        console.log(localStorage.getItem(key))
        var item = JSON.parse(localStorage.getItem(key));
        return item.value;
    }
    set(key, value) {
        localStorage.setItem(key, JSON.parse({
            "value": value,
            "timestamp": new Date()
        }));
    }
}
class Style {
    constructor() {
        this._styles = {}
        this._style_dom = document.createElement("style");
        this._themes = {}
        this._current_theme = null;
        this.applyTheme($configuration.get("theme", window.matchMedia("(prefers-color-scheme: dark)") ? "dark" : "light"))
        document.getElementsByTagName("head").item(0).appendChild(this._style_dom);
    }
    _parseToString(object) {
        if (Array.isArray(object)) {
            return object.map(this._parseToString).join(";");
        } else if (typeof object == "object") {
            return Object.entries(object).map(([key, value]) => `${key}:${this._parseToString(value)}`).join(";");
        } else {
            return object.toString();
        }
    }
    add(name, style) {
        this._styles[name] = this._parseToString(style);
        this.render();
    }
    addAll(styles) {
        Object.entries(styles).forEach(([name, style]) => this.add(name, style));
    }
    render() {
        const theme = {};
        Object.entries(this._themes[this._current_theme] || {}).forEach(([key, value]) => {
            theme[`--${key}`] = value;
        })
        this._styles[":root"] = this._parseToString(theme);
        const styleRule = Object.entries(this._styles).map(([name, style]) => style == null ? "" : `${name}{${style}}`).join("");
        if (!this._sheet_render(styleRule)) {
            while (this._style_dom.childNodes.length > 0) {
                this._style_dom.removeChild(this._style_dom.childNodes[0]);
            }
            this._style_dom.appendChild(document.createTextNode(styleRule));
        }
    }
    _sheet_render(styleRule) {
        const styleSheet = this._style_dom.sheet;
        if (styleSheet) {
            while (styleSheet.cssRules.length > 0) {
                styleSheet.deleteRule(0);
            }
            try {
                styleSheet.insertRule(this._sheet, styleSheet.cssRules.length);
                return true;
            } catch (e) {
            }
            return false;
        }
    }
    applyTheme(name) {
        this._current_theme = name || Object.keys(this._themes)[0];
        this.render();
    }
    setTheme(name, style) {
        this._themes[name] = style;
    }
}
class SVGContainers {
    static _parse(element) {
        return new Element(document.createRange().createContextualFragment(element).childNodes[0]);
    }
    static get menu() {
        return SVGContainers._parse('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"></path></svg>')
    }
    static get moon() {
        return SVGContainers._parse('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M12 11.807A9.002 9.002 0 0 1 10.049 2a9.942 9.942 0 0 0-5.12 2.735c-3.905 3.905-3.905 10.237 0 14.142 3.906 3.906 10.237 3.905 14.143 0a9.946 9.946 0 0 0 2.735-5.119A9.003 9.003 0 0 1 12 11.807z"></path></svg>')
    }
    static get sun() {
        return SVGContainers._parse('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M6.995 12c0 2.761 2.246 5.007 5.007 5.007s5.007-2.246 5.007-5.007-2.246-5.007-5.007-5.007S6.995 9.239 6.995 12zM11 19h2v3h-2zm0-17h2v3h-2zm-9 9h3v2H2zm17 0h3v2h-3zM5.637 19.778l-1.414-1.414 2.121-2.121 1.414 1.414zM16.242 6.344l2.122-2.122 1.414 1.414-2.122 2.122zM6.344 7.759 4.223 5.637l1.415-1.414 2.12 2.122zm13.434 10.605-1.414 1.414-2.122-2.122 1.414-1.414z"></path></svg>')
    }
}
function createElement(object) {
    return new Element(object);
}
const $configuration = new Configuration();
const $ElementManager = new ElementManager();
const $style = new Style();
const $i18n = new I18NManager();
const $socket = new Socket(window.location.origin + "/api");
$style.setTheme("light", {
    "main-color": "#ffffff",
    "color": "#000000",
    "background": "#F5F6F8"
})
$style.setTheme("dark", {
    "main-color": "#000000",
    "color": "#ffffff",
    "background": "#181818"
})
$style.addAll({
    "::-webkit-scrollbar, html ::-webkit-scrollbar": {
        "width": "5px",
        "height": "5px",
        "border-radius": "10px"
    },
    "::-webkit-scrollbar-thumb, html ::-webkit-scrollbar-thumb": {
        "box-shadow": "rgba(0, 0, 0, 0) 0px 0px 6px inset",
        "background-color": "rgb(102, 102, 102)",
        "border-radius": "10px",
    },
    "body": {
        "overflow": "hidden"
    },
    ".app": {
        "height": "100vh",
        "width": "100vw",
        "background": "var(--background)"
    },
    "header": `
        background-color: var(--background);
        text-align: center;
        min-height: 56px;
        width: 100%;
        padding: 8px;
        position: fixed;
        z-index: 1;
        display: flex;
        align-items: center;
        flex-wrap: nowrap;
        justify-content: space-between
    `,
    "header svg": {
        "width": "48px",
        "height": "48px",
        "padding": "8px", 
        "cursor": "pointer"
    },
    "svg": {
        "fill": "var(--color)"
    }
})
function load() {
    const $dom_body = new Element(document.body);

    const $app = createElement("div").classes("app")
    const $header = createElement("header")
    const $header_content_left = createElement("div").append(
        SVGContainers.menu,
        SVGContainers.sun
    )
    const $header_content_right = createElement("div");
    $header.append($header_content_left, $header_content_right);
    $app.append($header);

    $dom_body.appendBefore($app);
}
window.addEventListener("DOMContentLoaded", () => {
    load()
    Array.from(document.getElementsByClassName("preloader")).forEach(e => {
        const element = new Element(e);
        requestAnimationFrame(() => {
            element.classes("hidden");
            setTimeout(() => {
                element.remove();
            }, 1500)
        })
    })
})

globalThis.$configuration = $configuration;
globalThis.$ElementManager = $ElementManager;
globalThis.$style = $style;
globalThis.$i18n = $i18n;
globalThis.$socket = $socket;
