var __defProp = Object.defineProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};

// node_modules/@lit/reactive-element/css-tag.js
var t = window;
var e =
  t.ShadowRoot &&
  (void 0 === t.ShadyCSS || t.ShadyCSS.nativeShadow) &&
  "adoptedStyleSheets" in Document.prototype &&
  "replace" in CSSStyleSheet.prototype;
var s = /* @__PURE__ */ Symbol();
var n = /* @__PURE__ */ new WeakMap();
var o = class {
  constructor(t3, e5, n5) {
    if (((this._$cssResult$ = true), n5 !== s))
      throw Error(
        "CSSResult is not constructable. Use `unsafeCSS` or `css` instead.",
      );
    ((this.cssText = t3), (this.t = e5));
  }
  get styleSheet() {
    let t3 = this.o;
    const s6 = this.t;
    if (e && void 0 === t3) {
      const e5 = void 0 !== s6 && 1 === s6.length;
      (e5 && (t3 = n.get(s6)),
        void 0 === t3 &&
          ((this.o = t3 = new CSSStyleSheet()).replaceSync(this.cssText),
          e5 && n.set(s6, t3)));
    }
    return t3;
  }
  toString() {
    return this.cssText;
  }
};
var r = (t3) => new o("string" == typeof t3 ? t3 : t3 + "", void 0, s);
var i = (t3, ...e5) => {
  const n5 =
    1 === t3.length
      ? t3[0]
      : e5.reduce(
          (e6, s6, n6) =>
            e6 +
            ((t4) => {
              if (true === t4._$cssResult$) return t4.cssText;
              if ("number" == typeof t4) return t4;
              throw Error(
                "Value passed to 'css' function must be a 'css' function result: " +
                  t4 +
                  ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.",
              );
            })(s6) +
            t3[n6 + 1],
          t3[0],
        );
  return new o(n5, t3, s);
};
var S = (s6, n5) => {
  e
    ? (s6.adoptedStyleSheets = n5.map((t3) =>
        t3 instanceof CSSStyleSheet ? t3 : t3.styleSheet,
      ))
    : n5.forEach((e5) => {
        const n6 = document.createElement("style"),
          o5 = t.litNonce;
        (void 0 !== o5 && n6.setAttribute("nonce", o5),
          (n6.textContent = e5.cssText),
          s6.appendChild(n6));
      });
};
var c = e
  ? (t3) => t3
  : (t3) =>
      t3 instanceof CSSStyleSheet
        ? ((t4) => {
            let e5 = "";
            for (const s6 of t4.cssRules) e5 += s6.cssText;
            return r(e5);
          })(t3)
        : t3;

// node_modules/@lit/reactive-element/reactive-element.js
var s2;
var e2 = window;
var r2 = e2.trustedTypes;
var h = r2 ? r2.emptyScript : "";
var o2 = e2.reactiveElementPolyfillSupport;
var n2 = {
  toAttribute(t3, i5) {
    switch (i5) {
      case Boolean:
        t3 = t3 ? h : null;
        break;
      case Object:
      case Array:
        t3 = null == t3 ? t3 : JSON.stringify(t3);
    }
    return t3;
  },
  fromAttribute(t3, i5) {
    let s6 = t3;
    switch (i5) {
      case Boolean:
        s6 = null !== t3;
        break;
      case Number:
        s6 = null === t3 ? null : Number(t3);
        break;
      case Object:
      case Array:
        try {
          s6 = JSON.parse(t3);
        } catch (t4) {
          s6 = null;
        }
    }
    return s6;
  },
};
var a = (t3, i5) => i5 !== t3 && (i5 == i5 || t3 == t3);
var l = {
  attribute: true,
  type: String,
  converter: n2,
  reflect: false,
  hasChanged: a,
};
var d = "finalized";
var u = class extends HTMLElement {
  constructor() {
    (super(),
      (this._$Ei = /* @__PURE__ */ new Map()),
      (this.isUpdatePending = false),
      (this.hasUpdated = false),
      (this._$El = null),
      this._$Eu());
  }
  static addInitializer(t3) {
    var i5;
    (this.finalize(),
      (null !== (i5 = this.h) && void 0 !== i5 ? i5 : (this.h = [])).push(t3));
  }
  static get observedAttributes() {
    this.finalize();
    const t3 = [];
    return (
      this.elementProperties.forEach((i5, s6) => {
        const e5 = this._$Ep(s6, i5);
        void 0 !== e5 && (this._$Ev.set(e5, s6), t3.push(e5));
      }),
      t3
    );
  }
  static createProperty(t3, i5 = l) {
    if (
      (i5.state && (i5.attribute = false),
      this.finalize(),
      this.elementProperties.set(t3, i5),
      !i5.noAccessor && !this.prototype.hasOwnProperty(t3))
    ) {
      const s6 = "symbol" == typeof t3 ? /* @__PURE__ */ Symbol() : "__" + t3,
        e5 = this.getPropertyDescriptor(t3, s6, i5);
      void 0 !== e5 && Object.defineProperty(this.prototype, t3, e5);
    }
  }
  static getPropertyDescriptor(t3, i5, s6) {
    return {
      get() {
        return this[i5];
      },
      set(e5) {
        const r4 = this[t3];
        ((this[i5] = e5), this.requestUpdate(t3, r4, s6));
      },
      configurable: true,
      enumerable: true,
    };
  }
  static getPropertyOptions(t3) {
    return this.elementProperties.get(t3) || l;
  }
  static finalize() {
    if (this.hasOwnProperty(d)) return false;
    this[d] = true;
    const t3 = Object.getPrototypeOf(this);
    if (
      (t3.finalize(),
      void 0 !== t3.h && (this.h = [...t3.h]),
      (this.elementProperties = new Map(t3.elementProperties)),
      (this._$Ev = /* @__PURE__ */ new Map()),
      this.hasOwnProperty("properties"))
    ) {
      const t4 = this.properties,
        i5 = [
          ...Object.getOwnPropertyNames(t4),
          ...Object.getOwnPropertySymbols(t4),
        ];
      for (const s6 of i5) this.createProperty(s6, t4[s6]);
    }
    return ((this.elementStyles = this.finalizeStyles(this.styles)), true);
  }
  static finalizeStyles(i5) {
    const s6 = [];
    if (Array.isArray(i5)) {
      const e5 = new Set(i5.flat(1 / 0).reverse());
      for (const i6 of e5) s6.unshift(c(i6));
    } else void 0 !== i5 && s6.push(c(i5));
    return s6;
  }
  static _$Ep(t3, i5) {
    const s6 = i5.attribute;
    return false === s6
      ? void 0
      : "string" == typeof s6
        ? s6
        : "string" == typeof t3
          ? t3.toLowerCase()
          : void 0;
  }
  _$Eu() {
    var t3;
    ((this._$E_ = new Promise((t4) => (this.enableUpdating = t4))),
      (this._$AL = /* @__PURE__ */ new Map()),
      this._$Eg(),
      this.requestUpdate(),
      null === (t3 = this.constructor.h) ||
        void 0 === t3 ||
        t3.forEach((t4) => t4(this)));
  }
  addController(t3) {
    var i5, s6;
    ((null !== (i5 = this._$ES) && void 0 !== i5 ? i5 : (this._$ES = [])).push(
      t3,
    ),
      void 0 !== this.renderRoot &&
        this.isConnected &&
        (null === (s6 = t3.hostConnected) || void 0 === s6 || s6.call(t3)));
  }
  removeController(t3) {
    var i5;
    null === (i5 = this._$ES) ||
      void 0 === i5 ||
      i5.splice(this._$ES.indexOf(t3) >>> 0, 1);
  }
  _$Eg() {
    this.constructor.elementProperties.forEach((t3, i5) => {
      this.hasOwnProperty(i5) && (this._$Ei.set(i5, this[i5]), delete this[i5]);
    });
  }
  createRenderRoot() {
    var t3;
    const s6 =
      null !== (t3 = this.shadowRoot) && void 0 !== t3
        ? t3
        : this.attachShadow(this.constructor.shadowRootOptions);
    return (S(s6, this.constructor.elementStyles), s6);
  }
  connectedCallback() {
    var t3;
    (void 0 === this.renderRoot && (this.renderRoot = this.createRenderRoot()),
      this.enableUpdating(true),
      null === (t3 = this._$ES) ||
        void 0 === t3 ||
        t3.forEach((t4) => {
          var i5;
          return null === (i5 = t4.hostConnected) || void 0 === i5
            ? void 0
            : i5.call(t4);
        }));
  }
  enableUpdating(t3) {}
  disconnectedCallback() {
    var t3;
    null === (t3 = this._$ES) ||
      void 0 === t3 ||
      t3.forEach((t4) => {
        var i5;
        return null === (i5 = t4.hostDisconnected) || void 0 === i5
          ? void 0
          : i5.call(t4);
      });
  }
  attributeChangedCallback(t3, i5, s6) {
    this._$AK(t3, s6);
  }
  _$EO(t3, i5, s6 = l) {
    var e5;
    const r4 = this.constructor._$Ep(t3, s6);
    if (void 0 !== r4 && true === s6.reflect) {
      const h3 = (
        void 0 !==
        (null === (e5 = s6.converter) || void 0 === e5
          ? void 0
          : e5.toAttribute)
          ? s6.converter
          : n2
      ).toAttribute(i5, s6.type);
      ((this._$El = t3),
        null == h3 ? this.removeAttribute(r4) : this.setAttribute(r4, h3),
        (this._$El = null));
    }
  }
  _$AK(t3, i5) {
    var s6;
    const e5 = this.constructor,
      r4 = e5._$Ev.get(t3);
    if (void 0 !== r4 && this._$El !== r4) {
      const t4 = e5.getPropertyOptions(r4),
        h3 =
          "function" == typeof t4.converter
            ? { fromAttribute: t4.converter }
            : void 0 !==
                (null === (s6 = t4.converter) || void 0 === s6
                  ? void 0
                  : s6.fromAttribute)
              ? t4.converter
              : n2;
      ((this._$El = r4),
        (this[r4] = h3.fromAttribute(i5, t4.type)),
        (this._$El = null));
    }
  }
  requestUpdate(t3, i5, s6) {
    let e5 = true;
    (void 0 !== t3 &&
      (((s6 = s6 || this.constructor.getPropertyOptions(t3)).hasChanged || a)(
        this[t3],
        i5,
      )
        ? (this._$AL.has(t3) || this._$AL.set(t3, i5),
          true === s6.reflect &&
            this._$El !== t3 &&
            (void 0 === this._$EC && (this._$EC = /* @__PURE__ */ new Map()),
            this._$EC.set(t3, s6)))
        : (e5 = false)),
      !this.isUpdatePending && e5 && (this._$E_ = this._$Ej()));
  }
  async _$Ej() {
    this.isUpdatePending = true;
    try {
      await this._$E_;
    } catch (t4) {
      Promise.reject(t4);
    }
    const t3 = this.scheduleUpdate();
    return (null != t3 && (await t3), !this.isUpdatePending);
  }
  scheduleUpdate() {
    return this.performUpdate();
  }
  performUpdate() {
    var t3;
    if (!this.isUpdatePending) return;
    (this.hasUpdated,
      this._$Ei &&
        (this._$Ei.forEach((t4, i6) => (this[i6] = t4)), (this._$Ei = void 0)));
    let i5 = false;
    const s6 = this._$AL;
    try {
      ((i5 = this.shouldUpdate(s6)),
        i5
          ? (this.willUpdate(s6),
            null === (t3 = this._$ES) ||
              void 0 === t3 ||
              t3.forEach((t4) => {
                var i6;
                return null === (i6 = t4.hostUpdate) || void 0 === i6
                  ? void 0
                  : i6.call(t4);
              }),
            this.update(s6))
          : this._$Ek());
    } catch (t4) {
      throw ((i5 = false), this._$Ek(), t4);
    }
    i5 && this._$AE(s6);
  }
  willUpdate(t3) {}
  _$AE(t3) {
    var i5;
    (null === (i5 = this._$ES) ||
      void 0 === i5 ||
      i5.forEach((t4) => {
        var i6;
        return null === (i6 = t4.hostUpdated) || void 0 === i6
          ? void 0
          : i6.call(t4);
      }),
      this.hasUpdated || ((this.hasUpdated = true), this.firstUpdated(t3)),
      this.updated(t3));
  }
  _$Ek() {
    ((this._$AL = /* @__PURE__ */ new Map()), (this.isUpdatePending = false));
  }
  get updateComplete() {
    return this.getUpdateComplete();
  }
  getUpdateComplete() {
    return this._$E_;
  }
  shouldUpdate(t3) {
    return true;
  }
  update(t3) {
    (void 0 !== this._$EC &&
      (this._$EC.forEach((t4, i5) => this._$EO(i5, this[i5], t4)),
      (this._$EC = void 0)),
      this._$Ek());
  }
  updated(t3) {}
  firstUpdated(t3) {}
};
((u[d] = true),
  (u.elementProperties = /* @__PURE__ */ new Map()),
  (u.elementStyles = []),
  (u.shadowRootOptions = { mode: "open" }),
  null == o2 || o2({ ReactiveElement: u }),
  (null !== (s2 = e2.reactiveElementVersions) && void 0 !== s2
    ? s2
    : (e2.reactiveElementVersions = [])
  ).push("1.6.3"));

// node_modules/lit-html/lit-html.js
var t2;
var i2 = window;
var s3 = i2.trustedTypes;
var e3 = s3 ? s3.createPolicy("lit-html", { createHTML: (t3) => t3 }) : void 0;
var o3 = "$lit$";
var n3 = `lit$${crypto.getRandomValues(new Uint32Array(1))[0].toString(36)}$`;
var l2 = "?" + n3;
var h2 = `<${l2}>`;
var r3 = document;
var u2 = () => r3.createComment("");
var d2 = (t3) =>
  null === t3 || ("object" != typeof t3 && "function" != typeof t3);
var c2 = Array.isArray;
var v = (t3) =>
  c2(t3) || "function" == typeof (null == t3 ? void 0 : t3[Symbol.iterator]);
var a2 = "[ 	\n\f\r]";
var f = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g;
var _ = /-->/g;
var m = />/g;
var p = RegExp(
  `>|${a2}(?:([^\\s"'>=/]+)(${a2}*=${a2}*(?:[^ 	 // nosemgrep
\f\r"'\`<>=]|("|')|))|$)`,
  "g",
);
var g = /'/g;
var $ = /"/g;
var y = /^(?:script|style|textarea|title)$/i;
var w =
  (t3) =>
  (i5, ...s6) => ({ _$litType$: t3, strings: i5, values: s6 });
var x = w(1);
var b = w(2);
var T = /* @__PURE__ */ Symbol.for("lit-noChange");
var A = /* @__PURE__ */ Symbol.for("lit-nothing");
var E = /* @__PURE__ */ new WeakMap();
var C = r3.createTreeWalker(r3, 129, null, false);
function P(t3, i5) {
  if (!Array.isArray(t3) || !t3.hasOwnProperty("raw"))
    throw Error("invalid template strings array");
  return void 0 !== e3 ? e3.createHTML(i5) : i5;
}
var V = (t3, i5) => {
  const s6 = t3.length - 1,
    e5 = [];
  let l5,
    r4 = 2 === i5 ? "<svg>" : "",
    u3 = f;
  for (let i6 = 0; i6 < s6; i6++) {
    const s7 = t3[i6];
    let d3,
      c3,
      v2 = -1,
      a4 = 0;
    for (
      ;
      a4 < s7.length && ((u3.lastIndex = a4), (c3 = u3.exec(s7)), null !== c3);
    )
      ((a4 = u3.lastIndex),
        u3 === f
          ? "!--" === c3[1]
            ? (u3 = _)
            : void 0 !== c3[1]
              ? (u3 = m)
              : void 0 !== c3[2]
                ? (y.test(c3[2]) && (l5 = RegExp("</" + c3[2], "g")), (u3 = p))
                : void 0 !== c3[3] && (u3 = p)
          : u3 === p
            ? ">" === c3[0]
              ? ((u3 = null != l5 ? l5 : f), (v2 = -1))
              : void 0 === c3[1]
                ? (v2 = -2)
                : ((v2 = u3.lastIndex - c3[2].length),
                  (d3 = c3[1]),
                  (u3 = void 0 === c3[3] ? p : '"' === c3[3] ? $ : g))
            : u3 === $ || u3 === g
              ? (u3 = p)
              : u3 === _ || u3 === m
                ? (u3 = f)
                : ((u3 = p), (l5 = void 0))); // nosemgrep
    const w2 = u3 === p && t3[i6 + 1].startsWith("/>") ? " " : "";
    r4 +=
      u3 === f
        ? s7 + h2
        : v2 >= 0
          ? (e5.push(d3), s7.slice(0, v2) + o3 + s7.slice(v2) + n3 + w2)
          : s7 + n3 + (-2 === v2 ? (e5.push(void 0), i6) : w2);
  }
  return [P(t3, r4 + (t3[s6] || "<?>") + (2 === i5 ? "</svg>" : "")), e5];
};
var N = class _N {
  constructor({ strings: t3, _$litType$: i5 }, e5) {
    let h3;
    this.parts = [];
    let r4 = 0,
      d3 = 0;
    const c3 = t3.length - 1,
      v2 = this.parts,
      [a4, f2] = V(t3, i5);
    if (
      ((this.el = _N.createElement(a4, e5)),
      (C.currentNode = this.el.content),
      2 === i5)
    ) {
      const t4 = this.el.content,
        i6 = t4.firstChild;
      (i6.remove(), t4.append(...i6.childNodes));
    }
    for (; null !== (h3 = C.nextNode()) && v2.length < c3; ) {
      if (1 === h3.nodeType) {
        if (h3.hasAttributes()) {
          const t4 = [];
          for (const i6 of h3.getAttributeNames())
            if (i6.endsWith(o3) || i6.startsWith(n3)) {
              const s6 = f2[d3++];
              if ((t4.push(i6), void 0 !== s6)) {
                const t5 = h3.getAttribute(s6.toLowerCase() + o3).split(n3),
                  i7 = /([.?@])?(.*)/.exec(s6);
                v2.push({
                  type: 1,
                  index: r4,
                  name: i7[2],
                  strings: t5,
                  ctor:
                    "." === i7[1]
                      ? H
                      : "?" === i7[1]
                        ? L
                        : "@" === i7[1]
                          ? z
                          : k,
                });
              } else v2.push({ type: 6, index: r4 });
            }
          for (const i6 of t4) h3.removeAttribute(i6);
        }
        if (y.test(h3.tagName)) {
          const t4 = h3.textContent.split(n3),
            i6 = t4.length - 1;
          if (i6 > 0) {
            h3.textContent = s3 ? s3.emptyScript : "";
            for (let s6 = 0; s6 < i6; s6++)
              (h3.append(t4[s6], u2()),
                C.nextNode(),
                v2.push({ type: 2, index: ++r4 }));
            h3.append(t4[i6], u2());
          }
        }
      } else if (8 === h3.nodeType)
        if (h3.data === l2) v2.push({ type: 2, index: r4 });
        else {
          let t4 = -1;
          for (; -1 !== (t4 = h3.data.indexOf(n3, t4 + 1)); )
            (v2.push({ type: 7, index: r4 }), (t4 += n3.length - 1));
        }
      r4++;
    }
  }
  static createElement(t3, i5) {
    const s6 = r3.createElement("template");
    return ((s6.innerHTML = t3), s6);
  }
};
function S2(t3, i5, s6 = t3, e5) {
  var o5, n5, l5, h3;
  if (i5 === T) return i5;
  let r4 =
    void 0 !== e5
      ? null === (o5 = s6._$Co) || void 0 === o5
        ? void 0
        : o5[e5]
      : s6._$Cl;
  const u3 = d2(i5) ? void 0 : i5._$litDirective$;
  return (
    (null == r4 ? void 0 : r4.constructor) !== u3 &&
      (null === (n5 = null == r4 ? void 0 : r4._$AO) ||
        void 0 === n5 ||
        n5.call(r4, false),
      void 0 === u3 ? (r4 = void 0) : ((r4 = new u3(t3)), r4._$AT(t3, s6, e5)),
      void 0 !== e5
        ? ((null !== (l5 = (h3 = s6)._$Co) && void 0 !== l5
            ? l5
            : (h3._$Co = []))[e5] = r4)
        : (s6._$Cl = r4)),
    void 0 !== r4 && (i5 = S2(t3, r4._$AS(t3, i5.values), r4, e5)),
    i5
  );
}
var M = class {
  constructor(t3, i5) {
    ((this._$AV = []),
      (this._$AN = void 0),
      (this._$AD = t3),
      (this._$AM = i5));
  }
  get parentNode() {
    return this._$AM.parentNode;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  u(t3) {
    var i5;
    const {
        el: { content: s6 },
        parts: e5,
      } = this._$AD,
      o5 = (
        null !== (i5 = null == t3 ? void 0 : t3.creationScope) && void 0 !== i5
          ? i5
          : r3
      ).importNode(s6, true);
    C.currentNode = o5;
    let n5 = C.nextNode(),
      l5 = 0,
      h3 = 0,
      u3 = e5[0];
    for (; void 0 !== u3; ) {
      if (l5 === u3.index) {
        let i6;
        (2 === u3.type
          ? (i6 = new R(n5, n5.nextSibling, this, t3))
          : 1 === u3.type
            ? (i6 = new u3.ctor(n5, u3.name, u3.strings, this, t3))
            : 6 === u3.type && (i6 = new Z(n5, this, t3)),
          this._$AV.push(i6),
          (u3 = e5[++h3]));
      }
      l5 !== (null == u3 ? void 0 : u3.index) && ((n5 = C.nextNode()), l5++);
    }
    return ((C.currentNode = r3), o5);
  }
  v(t3) {
    let i5 = 0;
    for (const s6 of this._$AV)
      (void 0 !== s6 &&
        (void 0 !== s6.strings
          ? (s6._$AI(t3, s6, i5), (i5 += s6.strings.length - 2))
          : s6._$AI(t3[i5])),
        i5++);
  }
};
var R = class _R {
  constructor(t3, i5, s6, e5) {
    var o5;
    ((this.type = 2),
      (this._$AH = A),
      (this._$AN = void 0),
      (this._$AA = t3),
      (this._$AB = i5),
      (this._$AM = s6),
      (this.options = e5),
      (this._$Cp =
        null === (o5 = null == e5 ? void 0 : e5.isConnected) ||
        void 0 === o5 ||
        o5));
  }
  get _$AU() {
    var t3, i5;
    return null !==
      (i5 = null === (t3 = this._$AM) || void 0 === t3 ? void 0 : t3._$AU) &&
      void 0 !== i5
      ? i5
      : this._$Cp;
  }
  get parentNode() {
    let t3 = this._$AA.parentNode;
    const i5 = this._$AM;
    return (
      void 0 !== i5 &&
        11 === (null == t3 ? void 0 : t3.nodeType) &&
        (t3 = i5.parentNode),
      t3
    );
  }
  get startNode() {
    return this._$AA;
  }
  get endNode() {
    return this._$AB;
  }
  _$AI(t3, i5 = this) {
    ((t3 = S2(this, t3, i5)),
      d2(t3)
        ? t3 === A || null == t3 || "" === t3
          ? (this._$AH !== A && this._$AR(), (this._$AH = A))
          : t3 !== this._$AH && t3 !== T && this._(t3)
        : void 0 !== t3._$litType$
          ? this.g(t3)
          : void 0 !== t3.nodeType
            ? this.$(t3)
            : v(t3)
              ? this.T(t3)
              : this._(t3));
  }
  k(t3) {
    return this._$AA.parentNode.insertBefore(t3, this._$AB);
  }
  $(t3) {
    this._$AH !== t3 && (this._$AR(), (this._$AH = this.k(t3)));
  }
  _(t3) {
    (this._$AH !== A && d2(this._$AH)
      ? (this._$AA.nextSibling.data = t3)
      : this.$(r3.createTextNode(t3)),
      (this._$AH = t3));
  }
  g(t3) {
    var i5;
    const { values: s6, _$litType$: e5 } = t3,
      o5 =
        "number" == typeof e5
          ? this._$AC(t3)
          : (void 0 === e5.el &&
              (e5.el = N.createElement(P(e5.h, e5.h[0]), this.options)),
            e5);
    if ((null === (i5 = this._$AH) || void 0 === i5 ? void 0 : i5._$AD) === o5)
      this._$AH.v(s6);
    else {
      const t4 = new M(o5, this),
        i6 = t4.u(this.options);
      (t4.v(s6), this.$(i6), (this._$AH = t4));
    }
  }
  _$AC(t3) {
    let i5 = E.get(t3.strings);
    return (void 0 === i5 && E.set(t3.strings, (i5 = new N(t3))), i5);
  }
  T(t3) {
    c2(this._$AH) || ((this._$AH = []), this._$AR());
    const i5 = this._$AH;
    let s6,
      e5 = 0;
    for (const o5 of t3)
      (e5 === i5.length
        ? i5.push((s6 = new _R(this.k(u2()), this.k(u2()), this, this.options)))
        : (s6 = i5[e5]),
        s6._$AI(o5),
        e5++);
    e5 < i5.length &&
      (this._$AR(s6 && s6._$AB.nextSibling, e5), (i5.length = e5));
  }
  _$AR(t3 = this._$AA.nextSibling, i5) {
    var s6;
    for (
      null === (s6 = this._$AP) ||
      void 0 === s6 ||
      s6.call(this, false, true, i5);
      t3 && t3 !== this._$AB;
    ) {
      const i6 = t3.nextSibling;
      (t3.remove(), (t3 = i6));
    }
  }
  setConnected(t3) {
    var i5;
    void 0 === this._$AM &&
      ((this._$Cp = t3),
      null === (i5 = this._$AP) || void 0 === i5 || i5.call(this, t3));
  }
};
var k = class {
  constructor(t3, i5, s6, e5, o5) {
    ((this.type = 1),
      (this._$AH = A),
      (this._$AN = void 0),
      (this.element = t3),
      (this.name = i5),
      (this._$AM = e5),
      (this.options = o5),
      s6.length > 2 || "" !== s6[0] || "" !== s6[1]
        ? ((this._$AH = Array(s6.length - 1).fill(new String())),
          (this.strings = s6))
        : (this._$AH = A));
  }
  get tagName() {
    return this.element.tagName;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t3, i5 = this, s6, e5) {
    const o5 = this.strings;
    let n5 = false;
    if (void 0 === o5)
      ((t3 = S2(this, t3, i5, 0)),
        (n5 = !d2(t3) || (t3 !== this._$AH && t3 !== T)),
        n5 && (this._$AH = t3));
    else {
      const e6 = t3;
      let l5, h3;
      for (t3 = o5[0], l5 = 0; l5 < o5.length - 1; l5++)
        ((h3 = S2(this, e6[s6 + l5], i5, l5)),
          h3 === T && (h3 = this._$AH[l5]),
          n5 || (n5 = !d2(h3) || h3 !== this._$AH[l5]),
          h3 === A
            ? (t3 = A)
            : t3 !== A && (t3 += (null != h3 ? h3 : "") + o5[l5 + 1]),
          (this._$AH[l5] = h3));
    }
    n5 && !e5 && this.j(t3);
  }
  j(t3) {
    t3 === A
      ? this.element.removeAttribute(this.name)
      : this.element.setAttribute(this.name, null != t3 ? t3 : "");
  }
};
var H = class extends k {
  constructor() {
    (super(...arguments), (this.type = 3));
  }
  j(t3) {
    this.element[this.name] = t3 === A ? void 0 : t3;
  }
};
var I = s3 ? s3.emptyScript : "";
var L = class extends k {
  constructor() {
    (super(...arguments), (this.type = 4));
  }
  j(t3) {
    t3 && t3 !== A
      ? this.element.setAttribute(this.name, I)
      : this.element.removeAttribute(this.name);
  }
};
var z = class extends k {
  constructor(t3, i5, s6, e5, o5) {
    (super(t3, i5, s6, e5, o5), (this.type = 5));
  }
  _$AI(t3, i5 = this) {
    var s6;
    if (
      (t3 = null !== (s6 = S2(this, t3, i5, 0)) && void 0 !== s6 ? s6 : A) === T
    )
      return;
    const e5 = this._$AH,
      o5 =
        (t3 === A && e5 !== A) ||
        t3.capture !== e5.capture ||
        t3.once !== e5.once ||
        t3.passive !== e5.passive,
      n5 = t3 !== A && (e5 === A || o5);
    (o5 && this.element.removeEventListener(this.name, this, e5),
      n5 && this.element.addEventListener(this.name, this, t3),
      (this._$AH = t3));
  }
  handleEvent(t3) {
    var i5, s6;
    "function" == typeof this._$AH
      ? this._$AH.call(
          null !==
            (s6 =
              null === (i5 = this.options) || void 0 === i5
                ? void 0
                : i5.host) && void 0 !== s6
            ? s6
            : this.element,
          t3,
        )
      : this._$AH.handleEvent(t3);
  }
};
var Z = class {
  constructor(t3, i5, s6) {
    ((this.element = t3),
      (this.type = 6),
      (this._$AN = void 0),
      (this._$AM = i5),
      (this.options = s6));
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t3) {
    S2(this, t3);
  }
};
var j = {
  O: o3,
  P: n3,
  A: l2,
  C: 1,
  M: V,
  L: M,
  R: v,
  D: S2,
  I: R,
  V: k,
  H: L,
  N: z,
  U: H,
  F: Z,
};
var B = i2.litHtmlPolyfillSupport;
(null == B || B(N, R),
  (null !== (t2 = i2.litHtmlVersions) && void 0 !== t2
    ? t2
    : (i2.litHtmlVersions = [])
  ).push("2.8.0"));
var D = (t3, i5, s6) => {
  var e5, o5;
  const n5 =
    null !== (e5 = null == s6 ? void 0 : s6.renderBefore) && void 0 !== e5
      ? e5
      : i5;
  let l5 = n5._$litPart$;
  if (void 0 === l5) {
    const t4 =
      null !== (o5 = null == s6 ? void 0 : s6.renderBefore) && void 0 !== o5
        ? o5
        : null;
    n5._$litPart$ = l5 = new R(
      i5.insertBefore(u2(), t4),
      t4,
      void 0,
      null != s6 ? s6 : {},
    );
  }
  return (l5._$AI(t3), l5);
};

// node_modules/lit-element/lit-element.js
var l3;
var o4;
var s4 = class extends u {
  constructor() {
    (super(...arguments),
      (this.renderOptions = { host: this }),
      (this._$Do = void 0));
  }
  createRenderRoot() {
    var t3, e5;
    const i5 = super.createRenderRoot();
    return (
      (null !== (t3 = (e5 = this.renderOptions).renderBefore) &&
        void 0 !== t3) ||
        (e5.renderBefore = i5.firstChild),
      i5
    );
  }
  update(t3) {
    const i5 = this.render();
    (this.hasUpdated || (this.renderOptions.isConnected = this.isConnected),
      super.update(t3),
      (this._$Do = D(i5, this.renderRoot, this.renderOptions)));
  }
  connectedCallback() {
    var t3;
    (super.connectedCallback(),
      null === (t3 = this._$Do) || void 0 === t3 || t3.setConnected(true));
  }
  disconnectedCallback() {
    var t3;
    (super.disconnectedCallback(),
      null === (t3 = this._$Do) || void 0 === t3 || t3.setConnected(false));
  }
  render() {
    return T;
  }
};
((s4.finalized = true),
  (s4._$litElement$ = true),
  null === (l3 = globalThis.litElementHydrateSupport) ||
    void 0 === l3 ||
    l3.call(globalThis, { LitElement: s4 }));
var n4 = globalThis.litElementPolyfillSupport;
null == n4 || n4({ LitElement: s4 });
(null !== (o4 = globalThis.litElementVersions) && void 0 !== o4
  ? o4
  : (globalThis.litElementVersions = [])
).push("3.3.3");

// src/shared/design-tokens.css.js
var seloraTokens = i`
  :host {
    color-scheme: light dark;
    --selora-accent: #fbbf24;
    --selora-accent-text: #fbbf24;
    --selora-accent-dark: #f59e0b;
    --selora-accent-light: #fde68a;
    --selora-zinc-900: var(--primary-background-color, #18181b);
    --selora-zinc-800: var(--card-background-color, #27272a);
    --selora-zinc-700: var(--divider-color, #3f3f46);
    --selora-zinc-600: var(--secondary-text-color, #52525b);
    --selora-zinc-200: var(--primary-text-color, #e4e4e7);
    --selora-zinc-400: var(--secondary-text-color, #a1a1aa);
    --selora-glow: 0 0 20px rgba(251, 191, 36, 0.3);
    --selora-glow-lg: 0 0 40px rgba(251, 191, 36, 0.4);
    /* Section card = HA card bg, Inner card = HA page bg */
    --selora-section-bg: var(--card-background-color, #27272a);
    --selora-section-border: var(--divider-color, #3f3f46);
    --selora-inner-card-bg: var(--primary-background-color, #18181b);
    --selora-inner-card-border: var(--divider-color, #3f3f46);
    --selora-btn-outline-border: var(--divider-color, #3f3f46);
    --selora-btn-outline-text: var(--primary-text-color, #e4e4e7);
    font-family:
      Inter,
      system-ui,
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      Roboto,
      sans-serif;
  }
  * {
    font-family: inherit;
  }
`;

// src/shared/styles/animations.css.js
var sharedAnimations = i`
  @keyframes fadeInUp {
    from {
      opacity: 0;
      transform: translateY(18px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  @keyframes logoEntrance {
    0% {
      opacity: 0;
      transform: scale(0.6) translateY(12px);
    }
    60% {
      opacity: 1;
      transform: scale(1.06) translateY(-2px);
    }
    100% {
      opacity: 1;
      transform: scale(1) translateY(0);
    }
  }
  @keyframes highlightRow {
    0%,
    30% {
      background: rgba(251, 191, 36, 0.15);
    }
    100% {
      background: transparent;
    }
  }
  @keyframes fadeOutCard {
    to {
      opacity: 0;
      transform: scale(0.95);
    }
  }
  @keyframes slideInCard {
    from {
      opacity: 0;
      transform: translateX(30px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
  }
  @keyframes typingBounce {
    0%,
    80%,
    100% {
      transform: scale(0.6);
      opacity: 0.4;
    }
    40% {
      transform: scale(1);
      opacity: 1;
    }
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes bounce {
    0%,
    60%,
    100% {
      transform: translateY(0);
      opacity: 0.4;
    }
    30% {
      transform: translateY(-6px);
      opacity: 1;
    }
  }
  @keyframes gold-shift {
    0%,
    100% {
      background-position: 0% 50%;
    }
    50% {
      background-position: 100% 50%;
    }
  }
`;

// src/shared/styles/buttons.css.js
var sharedButtons = i`
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 10px 20px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    background: transparent;
    font-family: inherit;
    transition: colors 0.2s ease;
    user-select: none;
    color: var(--primary-text-color);
  }
  .btn:hover {
    opacity: 0.9;
  }
  .btn-primary {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #000;
    font-weight: 500;
  }
  .btn-primary:hover {
    box-shadow: var(--selora-glow);
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
    opacity: 1;
  }
  .btn-success {
    background: var(--success-color, #4caf50);
    border-color: var(--success-color, #4caf50);
    color: white;
  }
  .btn-outline {
    border-color: var(--selora-zinc-700);
    color: var(--selora-btn-outline-text);
    background: var(--selora-section-bg);
  }
  .btn-outline:hover {
    border-color: var(--selora-zinc-600);
    background: var(--secondary-background-color, #3f3f46);
  }
  .btn-danger {
    border-color: rgba(239, 68, 68, 0.4);
    color: var(--error-color, #ef4444);
    background: transparent;
  }
  .btn-danger:hover {
    background: rgba(239, 68, 68, 0.1);
    border-color: var(--error-color, #ef4444);
  }
  .btn-warning {
    border-color: rgba(251, 191, 36, 0.4);
    color: var(--selora-accent-text);
    background: transparent;
  }
  .btn-warning:hover {
    background: rgba(251, 191, 36, 0.08);
    border-color: var(--selora-accent);
  }
  .btn-ghost {
    border-color: transparent;
    color: var(--secondary-text-color);
    background: transparent;
    font-size: 11px;
    padding: 4px 8px;
  }
  .btn-ghost:hover {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.06);
    border-color: var(--divider-color);
  }
  .btn-ghost.active {
    color: var(--selora-accent-text);
    border-color: rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.05);
  }
`;

// src/shared/styles/modals.css.js
var sharedModals = i`
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 10001;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal-content {
    background: var(--card-background-color, #fff);
    border-radius: 16px;
    border: 1px solid var(--selora-zinc-800);
    padding: 24px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    width: 90%;
  }
  .modal {
    background: var(--card-background-color, #fff);
    border-radius: 16px;
    border: 1px solid var(--divider-color);
    padding: 24px;
    max-width: 420px;
    width: 90%;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }
  .modal-title {
    font-size: 18px;
    font-weight: 700;
    margin: 0 0 16px;
  }
  .modal-label {
    font-size: 13px;
    font-weight: 500;
    display: block;
    margin-bottom: 6px;
  }
  .modal-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .modal-input {
    flex: 1;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }
  .modal-input:focus {
    border-color: var(--selora-accent);
  }
  .modal-input::placeholder {
    opacity: 0.35;
  }
  .modal-input.generating-placeholder {
    display: flex;
    align-items: center;
    gap: 8px;
    border-color: var(--selora-accent);
  }
  .modal-row.generating .modal-magic-btn {
    border-color: var(--selora-accent);
  }
  .modal-magic-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 8px 10px;
    flex-shrink: 0;
    border-radius: 6px;
    border: 1.5px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
    font-weight: 600;
    transition: opacity 0.15s;
  }
  .modal-magic-btn:hover {
    opacity: 0.85;
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .modal-magic-btn ha-icon {
    --mdc-icon-size: 20px;
  }
  .modal-actions {
    display: flex;
    gap: 8px;
    margin-top: 16px;
    justify-content: flex-end;
  }
  .modal-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    border: 1.5px solid transparent;
    background: transparent;
    transition:
      background 0.15s,
      opacity 0.15s;
    user-select: none;
    letter-spacing: normal;
  }
  .modal-btn:hover {
    opacity: 0.85;
  }
  .modal-btn ha-icon {
    --mdc-icon-size: 14px;
  }
  .modal-cancel {
    border-color: var(--divider-color);
    color: var(--primary-text-color);
    background: var(--card-background-color);
  }
  .modal-cancel:hover {
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .modal-create {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #1a1a1a;
  }
  .modal-create:hover:not(:disabled) {
    box-shadow: var(--selora-glow);
    background: var(--selora-accent-light);
  }
  .modal-create:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

// src/shared/styles/badges.css.js
var sharedBadges = i`
  .badge {
    background: var(--selora-zinc-700);
    color: var(--selora-zinc-200);
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 12px;
    font-weight: 500;
    min-width: 16px;
    text-align: center;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    transition: all 0.25s ease;
  }
  .chip {
    padding: 3px 9px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 700;
    color: white;
  }
  .chip.ai-managed {
    background: var(--selora-accent);
  }
  .chip.user-managed {
    background: var(--selora-zinc-600);
  }
  .chip.suggestion {
    background: var(--selora-accent);
  }
  .status-indicator {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    flex-shrink: 0;
  }
  .status-indicator.on {
    color: var(--success-color, #4caf50);
    background: rgba(76, 175, 80, 0.12);
  }
  .status-indicator.off {
    color: var(--secondary-text-color);
    background: rgba(158, 158, 158, 0.12);
  }
`;

// src/shared/styles/loaders.css.js
var sharedLoaders = i`
  .spinner {
    display: inline-block;
    width: 18px;
    height: 18px;
    border: 2.5px solid rgba(0, 0, 0, 0.1);
    border-top-color: var(--selora-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  .spinner.green {
    border-color: rgba(76, 175, 80, 0.2);
    border-top-color: var(--success-color, #4caf50);
  }
  .dots-loader {
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .dots-loader span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--selora-accent);
    animation: bounce 1.2s ease-in-out infinite;
  }
  .dots-loader span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .dots-loader span:nth-child(3) {
    animation-delay: 0.4s;
  }
  .loading-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 0;
    font-size: 12px;
    opacity: 0.6;
  }
  .empty-row {
    padding: 12px 0;
    font-size: 12px;
    opacity: 0.5;
    font-style: italic;
  }
  .generating-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 0;
    font-size: 12px;
    opacity: 0.7;
  }
`;

// src/shared/styles/scrollbar.css.js
var sharedScrollbar = i`
  ::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  ::-webkit-scrollbar-track {
    background: transparent;
  }
  ::-webkit-scrollbar-thumb {
    background: var(--selora-accent);
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: var(--selora-accent-light);
  }
  * {
    scrollbar-width: thin;
    scrollbar-color: var(--selora-accent) transparent;
  }
  ::selection {
    background: rgba(251, 191, 36, 0.3);
    color: inherit;
  }
  .gold-text {
    background-image: linear-gradient(
      90deg,
      #f59e0b,
      #fbbf24,
      #fde68a,
      #f59e0b
    );
    background-size: 300% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    animation: gold-shift 20s ease-in-out infinite;
  }
`;

// src/panel/styles/layout.css.js
var layoutStyles = i`
  :host {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
  }

  /* ---- Main area ---- */
  .body {
    flex: 1;
    display: flex;
    overflow: hidden;
  }
  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ---- Scroll view (automations / settings) ---- */
  .scroll-view {
    flex: 1;
    overflow-y: auto;
    padding: 24px 28px;
    max-width: 1200px;
    margin: 0 auto;
    width: 100%;
    box-sizing: border-box;
  }

  /* ---- Section cards ---- */
  .section-card {
    background: var(--selora-section-bg);
    color: var(--primary-text-color);
    border: 1px solid var(--selora-section-border);
    border-radius: 20px;
    padding: 28px 32px;
    margin-bottom: 36px;
  }
  .section-card .card {
    background: var(--selora-inner-card-bg);
    border: 1px solid var(--selora-inner-card-border);
    border-radius: 14px;
  }
  .section-card .automations-list {
    border-color: var(--selora-inner-card-border);
  }
  .section-card .auto-row {
    border-color: var(--selora-inner-card-border);
  }
  .section-card-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
  }
  .section-card-header h3 {
    font-size: 20px;
    margin: 0;
    font-weight: 700;
  }
  .section-card-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin-bottom: 24px;
  }
  @media (max-width: 600px) {
    .scroll-view {
      padding: 12px 10px;
    }
    .section-card {
      padding: 14px 12px;
      border-radius: 12px;
      margin-bottom: 16px;
    }
    .section-card .card {
      padding: 12px;
    }
  }
  .suggestions-section {
  }
  .show-more-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--selora-accent-text);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    background: none;
    border: none;
    padding: 8px 0;
    font-family: inherit;
  }
  .show-more-link:hover {
    text-decoration: underline;
  }

  /* Narrow overrides — sidebar overlays on small screens */
  :host([narrow]) .body {
    position: relative;
  }
  :host([narrow]) .sidebar {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    z-index: 10;
    width: 0;
    min-width: 0;
    transform: translateX(-100%);
    transition:
      transform 0.25s ease,
      width 0.25s ease,
      min-width 0.25s ease;
    box-shadow: 2px 0 8px rgba(0, 0, 0, 0.2);
  }
  :host([narrow]) .sidebar.open {
    width: 260px;
    min-width: 260px;
    transform: translateX(0);
  }

  .toast {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 10050;
    max-width: min(420px, calc(100vw - 32px));
    padding: 10px 12px;
    border-radius: 10px;
    color: #fff;
    font-size: 13px;
    line-height: 1.4;
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .toast.info {
    background: #1f6feb;
  }
  .toast.success {
    background: #198754;
  }
  .toast.error {
    background: #dc3545;
  }
  .toast-close {
    margin-left: auto;
    cursor: pointer;
    opacity: 0.85;
  }
  .toast-close:hover {
    opacity: 1;
  }
`;

// src/panel/styles/sidebar.css.js
var sidebarStyles = i`
  .sidebar {
    width: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    background: var(--sidebar-background-color, var(--card-background-color));
    border-right: 1px solid var(--selora-zinc-800);
    overflow: hidden;
    transition:
      width 0.3s ease,
      min-width 0.3s ease;
  }
  .sidebar.open {
    width: 260px;
    min-width: 260px;
  }
  .sidebar-header {
    padding: 16px;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: normal;
    opacity: 0.6;
    border-bottom: 1px solid var(--divider-color);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .session-list {
    flex: 1;
    overflow-y: auto;
  }
  .session-item-wrapper {
    position: relative;
    overflow: hidden;
    border-bottom: 1px solid var(--divider-color);
  }
  .session-item-delete-bg {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 80px;
    background: var(--error-color, #ef4444);
    display: none;
    align-items: center;
    justify-content: center;
    color: white;
    --mdc-icon-size: 20px;
  }
  .session-item-wrapper.reveal-delete .session-item-delete-bg {
    display: flex;
  }
  .session-item {
    padding: 12px 16px;
    cursor: pointer;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    position: relative;
    transition:
      background 0.15s,
      transform 0.2s ease;
    background: var(--sidebar-background-color, var(--card-background-color));
    z-index: 1;
  }
  .session-item:hover {
    background: var(--secondary-background-color);
  }
  .session-item.active {
    background: rgba(251, 191, 36, 0.1);
    border-left: 3px solid var(--selora-accent);
    box-shadow: inset 0 0 12px rgba(251, 191, 36, 0.06);
  }
  .session-item.swiped {
    transform: translateX(-80px);
  }
  .session-title {
    font-size: 13px;
    font-weight: 500;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .session-meta {
    font-size: 11px;
    opacity: 0.5;
    margin-top: 2px;
  }
  .session-delete {
    opacity: 0;
    cursor: pointer;
    color: var(--error-color, #f44336);
    transition: opacity 0.15s;
    flex-shrink: 0;
    align-self: center;
  }
  .session-item:hover .session-delete {
    opacity: 0.6;
  }
  .session-delete:hover {
    opacity: 1 !important;
  }
  @media (pointer: coarse) {
    .session-delete {
      display: none;
    }
  }
  .sidebar-select-btn {
    background: transparent;
    border: 1px solid var(--divider-color);
    color: var(--primary-text-color);
    font-size: 11px;
    font-weight: 700;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition:
      background 0.15s,
      border-color 0.15s;
  }
  .sidebar-select-btn:hover {
    background: rgba(251, 191, 36, 0.1);
    border-color: var(--selora-accent);
  }
  .select-actions-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 16px;
    border-bottom: 1px solid var(--divider-color);
    background: rgba(251, 191, 36, 0.06);
  }
  .select-all-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    cursor: pointer;
    user-select: none;
  }
  .select-all-label input[type="checkbox"] {
    accent-color: var(--selora-accent);
    cursor: pointer;
  }
  .btn-delete-selected {
    display: flex;
    align-items: center;
    gap: 4px;
    background: transparent;
    border: 1px solid var(--error-color, #ef4444);
    color: var(--error-color, #ef4444);
    font-size: 11px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 6px;
    cursor: pointer;
    transition:
      background 0.15s,
      color 0.15s;
  }
  .btn-delete-selected:hover:not([disabled]) {
    background: var(--error-color, #ef4444);
    color: #fff;
  }
  .btn-delete-selected[disabled] {
    opacity: 0.35;
    cursor: not-allowed;
  }
  .session-checkbox {
    accent-color: var(--selora-accent);
    cursor: pointer;
    flex-shrink: 0;
    margin-top: 2px;
  }
  .new-chat-btn {
    margin: 12px;
    display: block;
  }
`;

// src/panel/styles/header.css.js
var headerStyles = i`
  .header {
    background: var(--app-header-background-color, #1c1c1e);
    color: var(--app-header-text-color, #e4e4e7);
    box-shadow: none;
    border-bottom: 1px solid var(--divider-color);
    z-index: 2;
    flex-shrink: 0;
  }
  .header-top {
    padding: 14px 24px;
    font-size: 20px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .header-top ha-icon-button {
    margin-right: 4px;
    display: inline-flex;
    opacity: 0.55;
  }
  .feedback-link {
    margin-left: auto;
    background: none;
    border: none;
    color: var(--primary-text-color);
    opacity: 0.45;
    font-size: 12px;
    cursor: pointer;
    padding: 4px 0;
    font-family: inherit;
    transition: opacity 0.15s;
  }
  .feedback-link:hover {
    opacity: 0.8;
    text-decoration: underline;
  }
  .tabs {
    display: flex;
    padding: 0 24px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .tab {
    padding: 10px 16px;
    cursor: pointer;
    font-weight: 400;
    font-size: 16px;
    opacity: 0.55;
    transition:
      opacity 0.3s,
      color 0.3s;
  }
  .tab:hover {
    opacity: 1;
    color: var(--selora-accent-text);
  }
  .tab.active {
    opacity: 1;
    font-weight: 600;
    color: var(--selora-accent-text);
  }
  .tab:first-child {
    padding-left: 0;
  }
  .tab-inner {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .tab-icon {
    --mdc-icon-size: 16px;
    margin-bottom: 12px;
  }
  .tab-text {
    position: relative;
    padding-bottom: 6px;
  }
  /* Shared underline-from-center effect */
  .tab-text::after,
  .card-tab::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: var(--selora-accent);
    transform: scaleX(0);
    transform-origin: center;
    transition: transform 0.3s ease;
  }
  .tab:hover .tab-text::after,
  .tab.active .tab-text::after,
  .card-tab.active::after {
    transform: scaleX(1);
  }
  .card-tab:hover::after {
    transform: scaleX(0.6);
  }
`;

// src/panel/styles/chat.css.js
var chatStyles = i`
  .chat-pane {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .empty-state {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    opacity: 0.45;
    gap: 12px;
    padding: 32px;
    text-align: center;
    animation: fadeInUp 0.5s ease both;
  }
  .empty-state.welcome {
    opacity: 1;
    gap: 0;
    justify-content: flex-start;
    padding: 12px;
  }
  @media (max-width: 600px) {
    .empty-state.welcome {
      padding: 4px;
    }
    .empty-state.welcome .section-card {
      padding: 16px;
    }
  }
  .empty-state.welcome > * {
    animation: fadeInUp 0.5s ease both;
  }
  .empty-state.welcome > img:first-child {
    animation: logoEntrance 0.7s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  }
  .empty-state.welcome > :nth-child(2) {
    animation-delay: 0.15s;
  }
  .empty-state.welcome > :nth-child(3) {
    animation-delay: 0.25s;
  }
  .empty-state.welcome > :nth-child(4) {
    animation-delay: 0.35s;
  }
  .empty-state.welcome > :nth-child(5) {
    animation-delay: 0.4s;
  }
  .empty-state.welcome > :nth-child(6) {
    animation-delay: 0.45s;
  }
  .empty-state.welcome > :nth-child(7) {
    animation-delay: 0.5s;
  }
  .empty-state ha-icon {
    --mdc-icon-size: 56px;
  }
  .welcome-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }
  .welcome-card:active {
    transform: translateY(0);
  }
  .message-row {
    display: flex;
    flex-direction: column;
  }
  .bubble {
    max-width: 82%;
    padding: 12px 16px;
    border-radius: 16px;
    font-size: 14px;
    line-height: 1.5;
    word-wrap: break-word;
  }
  .bubble.user {
    align-self: flex-end;
    background: var(--selora-zinc-800) !important;
    color: var(--selora-zinc-200) !important;
    border: 1px solid var(--selora-accent) !important;
    border-bottom-right-radius: 4px;
  }
  .bubble.assistant {
    align-self: flex-start;
    background: var(--card-background-color);
    box-shadow: var(--card-box-shadow);
    border: 1px solid var(--selora-zinc-700);
    border-bottom-left-radius: 4px;
  }
  .bubble-meta {
    font-size: 10px;
    opacity: 0.5;
    margin-top: 2px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .bubble.user + .bubble-meta {
    align-self: flex-end;
  }
  .bubble.assistant + .bubble-meta {
    align-self: flex-start;
  }
  .copy-msg-row {
    display: flex;
    justify-content: flex-end;
    margin-top: 4px;
  }
  .copy-msg-btn {
    background: none;
    border: none;
    padding: 2px 4px;
    cursor: pointer;
    opacity: 0;
    transition:
      opacity 0.15s,
      color 0.15s;
    color: inherit;
    line-height: 1;
    border-radius: 4px;
  }
  .message-row:hover .copy-msg-btn {
    opacity: 0.7;
  }
  .copy-msg-btn:hover {
    opacity: 1 !important;
  }
  .copy-msg-btn.copied {
    opacity: 1 !important;
    color: var(--success-color, #4caf50);
  }
  .bubble.assistant strong {
    color: var(--selora-accent-text);
  }

  /* ---- Chat input ---- */
  .chat-input-wrapper {
    border-top: 1px solid var(--divider-color);
    flex-shrink: 0;
  }
  .chat-input {
    padding: 16px 24px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
    background: transparent;
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .chat-input ha-textfield {
    --mdc-text-field-fill-color: var(--selora-zinc-800, #27272a);
    --mdc-text-field-ink-color: var(--primary-text-color);
    --mdc-text-field-label-ink-color: var(--secondary-text-color);
    --mdc-text-field-idle-line-color: var(--selora-zinc-700, #3f3f46);
    --mdc-text-field-hover-line-color: var(--selora-accent);
    border-radius: 12px;
    overflow: hidden;
  }
  .chat-input ha-icon-button {
    color: var(--selora-accent-text);
    opacity: 0.7;
    transition: opacity 0.2s;
  }
  .chat-input ha-icon-button:hover {
    opacity: 1;
  }
  .typing-bubble {
    align-self: flex-start;
    background-color: var(--card-background-color);
    box-shadow: var(--card-box-shadow);
    border-radius: 18px;
    border-bottom-left-radius: 4px;
    padding: 16px 22px;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .typing-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background-color: var(--secondary-text-color);
    animation: typingBounce 1.4s infinite ease-in-out both;
  }
  .typing-dot:nth-child(1) {
    animation-delay: 0s;
  }
  .typing-dot:nth-child(2) {
    animation-delay: 0.2s;
  }
  .typing-dot:nth-child(3) {
    animation-delay: 0.4s;
  }
  .streaming-cursor::after {
    content: "";
    display: inline-block;
    width: 2px;
    height: 1em;
    background-color: var(--primary-text-color);
    margin-left: 2px;
    vertical-align: text-bottom;
    animation: blink 0.7s step-end infinite;
  }
`;

// src/panel/styles/proposals.css.js
var proposalStyles = i`
  /* ---- Automation proposal card ---- */
  .proposal-card {
    margin-top: 12px;
    border: 1px solid rgba(251, 191, 36, 0.25);
    border-radius: 16px;
    overflow: hidden;
    background: var(--primary-background-color);
  }
  .proposal-header {
    background: rgba(251, 191, 36, 0.08);
    padding: 10px 14px;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: normal;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--selora-accent-text);
  }
  .proposal-body {
    padding: 14px;
  }
  .proposal-body .flow-chart {
    align-items: flex-start;
  }
  .proposal-body .flow-section {
    text-align: left;
  }
  .proposal-name {
    font-weight: 600;
    font-size: 15px;
    margin-bottom: 8px;
  }
  .proposal-description {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin-bottom: 12px;
    line-height: 1.5;
    padding: 10px 12px;
    background: rgba(251, 191, 36, 0.06);
    border-left: 3px solid var(--selora-accent);
    border-radius: 0 8px 8px 0;
  }
  .proposal-description-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: normal;
    opacity: 0.6;
    margin-bottom: 4px;
  }
  .yaml-toggle {
    font-size: 12px;
    cursor: pointer;
    opacity: 0.6;
    display: flex;
    align-items: center;
    gap: 4px;
    margin-bottom: 8px;
    user-select: none;
  }
  .yaml-toggle:hover {
    opacity: 1;
  }
  ha-code-editor {
    --code-mirror-font-size: 12px;
    --code-mirror-height: auto;
    font-size: 12px;
  }
  textarea.yaml-editor {
    width: 100%;
    box-sizing: border-box;
    background: var(--selora-zinc-900);
    color: var(--selora-zinc-200);
    padding: 10px 12px;
    border-radius: 8px;
    font-size: 11px;
    font-family: "Fira Code", "Cascadia Code", monospace;
    line-height: 1.5;
    border: 1px solid var(--selora-zinc-800);
    resize: vertical;
    min-height: 140px;
    outline: none;
    transition: border-color 0.3s;
  }
  textarea.yaml-editor:focus {
    border-color: var(--selora-accent);
    background: var(--selora-zinc-800);
  }
  .yaml-edit-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    flex-wrap: wrap;
  }
  .yaml-unsaved {
    font-size: 11px;
    color: var(--warning-color, #ff9800);
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
  }
  pre.yaml {
    background: var(--selora-zinc-900);
    color: var(--selora-zinc-200);
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--selora-zinc-800);
    font-size: 11px;
    overflow-x: auto;
    font-family: "Fira Code", "Cascadia Code", monospace;
    margin: 0 0 12px;
    max-height: 200px;
    overflow-y: auto;
  }
  .proposal-verify {
    font-size: 12px;
    font-style: italic;
    opacity: 0.65;
    margin-bottom: 10px;
  }
  .proposal-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .proposal-actions mwc-button[raised] {
    --mdc-theme-primary: var(--success-color, #4caf50);
  }

  /* Declined / saved states */
  .proposal-status {
    padding: 8px 12px;
    font-size: 12px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .proposal-status.saved {
    background: rgba(76, 175, 80, 0.12);
    color: var(--success-color, #4caf50);
  }
  .proposal-status.declined {
    background: rgba(158, 158, 158, 0.12);
    color: var(--secondary-text-color);
  }

  /* ---- Automation flowchart ---- */
  .flow-chart {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 10px 0 12px;
    font-size: 12px;
  }
  .flow-section {
    width: 100%;
    text-align: center;
  }
  .flow-label {
    font-size: 9px;
    font-weight: 800;
    letter-spacing: normal;
    text-transform: uppercase;
    opacity: 0.5;
    margin-bottom: 4px;
  }
  .flow-node {
    display: inline-block;
    padding: 6px 12px;
    border-radius: 8px;
    margin-bottom: 4px;
    max-width: 100%;
    word-break: break-word;
    font-size: 12px;
    line-height: 1.4;
  }
  .flow-node + .flow-node {
    margin-top: 3px;
  }
  .trigger-node,
  .condition-node,
  .action-node {
    background: rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.06);
    border: 1px solid rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.15);
    color: var(--primary-text-color);
  }
  .flow-arrow {
    font-size: 16px;
    line-height: 1;
    opacity: 0.35;
    padding: 3px 0;
    text-align: center;
  }
  .flow-arrow-sm {
    font-size: 13px;
    line-height: 1;
    opacity: 0.3;
    padding: 2px 0;
    text-align: center;
  }

  /* ---- Toggle switch ---- */
  .toggle-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 10px;
  }
  .toggle-switch {
    position: relative;
    width: 40px;
    height: 22px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .toggle-switch input {
    opacity: 0;
    width: 0;
    height: 0;
    position: absolute;
  }
  .toggle-track {
    position: absolute;
    inset: 0;
    border-radius: 11px;
    background: var(--divider-color);
    border: 1px solid rgba(0, 0, 0, 0.15);
    transition: background 0.2s;
  }
  .toggle-track.on {
    background: var(--selora-accent);
    border-color: var(--selora-accent-dark);
    box-shadow: 0 0 8px rgba(251, 191, 36, 0.35);
  }
  .toggle-thumb {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: white;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    transition: left 0.2s;
  }
  .toggle-track.on .toggle-thumb {
    left: 20px;
  }
  .toggle-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--secondary-text-color);
  }
  .toggle-label.on {
    color: var(--selora-accent-text);
  }
`;

// src/panel/styles/cards.css.js
var cardElementStyles = i`
  /* ---- Card action buttons ---- */
  .card-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--divider-color);
  }

  /* ---- Burger menu ---- */
  .burger-menu-wrapper {
    position: relative;
  }
  .burger-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    cursor: pointer;
    color: var(--secondary-text-color);
    transition: background 0.15s;
  }
  .burger-btn:hover {
    background: rgba(0, 0, 0, 0.06);
    color: var(--primary-text-color);
  }
  .burger-dropdown {
    position: absolute;
    right: 0;
    top: 32px;
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    z-index: 100;
    min-width: 140px;
    overflow: hidden;
  }
  .burger-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    color: var(--primary-text-color);
    border: none;
    background: none;
    width: 100%;
    text-align: left;
  }
  .burger-item:hover {
    background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.08);
  }
  .burger-item.danger {
    color: var(--error-color, #f44336);
  }
  .burger-item.danger:hover {
    background: rgba(244, 67, 54, 0.08);
  }
  .rename-input {
    flex: 1;
    font-size: 14px;
    font-weight: 600;
    border: 1px solid var(--selora-accent);
    border-radius: 8px;
    padding: 4px 8px;
    outline: none;
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    min-width: 0;
    transition: border-color 0.3s;
  }
  .rename-save-btn {
    background: var(--selora-accent);
    border: none;
    border-radius: 8px;
    color: #fff;
    cursor: pointer;
    padding: 4px 6px;
    margin-left: 4px;
    line-height: 1;
    display: flex;
    align-items: center;
    transition: background 0.3s;
  }
  .rename-save-btn:hover {
    background: #d97706;
    box-shadow: var(--selora-glow);
  }

  /* ---- Card inline tabs (Flow / YAML / History) ---- */
  .card-tabs {
    display: flex;
    align-items: center;
    gap: 0;
    margin: 8px 0 0;
    border-top: 1px solid var(--divider-color);
    padding-top: 8px;
    padding-bottom: 8px;
    font-size: 12px;
  }
  .card-tabs .label {
    font-size: 11px;
    opacity: 0.5;
    margin-right: 8px;
    white-space: nowrap;
  }
  .card-tab {
    padding: 4px 10px;
    border: none;
    background: none;
    font-size: 12px;
    font-weight: 500;
    color: var(--secondary-text-color);
    cursor: pointer;
    position: relative;
    transition: color 0.3s ease;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .card-tab:hover,
  .card-tab.active {
    color: var(--selora-accent-text);
  }
  .card-chevron {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.25s ease;
    cursor: pointer;
    opacity: 0.5;
    --mdc-icon-size: 16px;
    flex-shrink: 0;
  }
  .card-chevron:hover {
    opacity: 0.8;
  }
  .card-chevron.open {
    transform: rotate(180deg);
  }
  .card-tab-sep {
    color: var(--divider-color);
    font-size: 12px;
    user-select: none;
  }

  .expand-toggle {
    font-size: 11px;
    opacity: 0.55;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    user-select: none;
    padding: 4px 0;
  }
  .expand-toggle:hover {
    opacity: 1;
  }

  /* ---- Card base ---- */
  .card {
    background: var(--selora-zinc-800);
    color: var(--primary-text-color);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 14px;
    box-shadow: none;
    border: 1px solid var(--selora-zinc-700);
    transition: border-color 0.3s ease;
  }
  .card:hover {
    border-color: rgba(251, 191, 36, 0.3);
  }
  .card-row2 {
    position: relative;
  }
  .card .card-desc {
    transition: opacity 0.2s;
  }
  .card .card-actions-row {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    left: 0;
    right: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s;
  }
  .card:hover .card-desc {
    opacity: 0;
  }
  .card:hover .card-actions-row,
  .card .card-actions-row.visible {
    opacity: 1;
    pointer-events: auto;
  }
  .card.expanded .card-desc {
    opacity: 0;
  }
  @media (max-width: 600px) {
    .card-row2 {
      position: static !important;
      flex: none !important;
    }
    .card .card-desc {
      opacity: 1 !important;
      height: auto !important;
    }
    .card:hover .card-desc {
      opacity: 1 !important;
    }
    .card.expanded .card-desc {
      display: none;
    }
    .card .card-actions-row {
      position: static;
      top: auto;
      transform: none;
      opacity: 1;
      pointer-events: auto;
      margin-top: 10px;
    }
    .card .card-chevron {
      --mdc-icon-size: 22px;
      padding: 6px;
    }
    .card .burger-btn {
      width: 36px;
      height: 36px;
    }
    .card .card-actions-row > div:last-child {
      gap: 14px !important;
    }
    .card .refine-btn {
      font-size: 14px !important;
      padding: 10px 16px !important;
    }
    .burger-dropdown {
      min-width: 180px;
    }
    .burger-item {
      padding: 14px 18px;
      font-size: 15px;
      gap: 10px;
    }
    .burger-item ha-icon {
      --mdc-icon-size: 18px;
    }
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 10px;
    gap: 10px;
  }
  .card h3 {
    margin: 0;
    font-size: 16px;
  }
  .card p {
    margin: 6px 0;
    color: var(--secondary-text-color);
    font-size: 13px;
  }
  pre {
    background: var(--selora-zinc-900);
    color: var(--selora-zinc-200);
    padding: 10px;
    border-radius: 8px;
    border: 1px solid var(--selora-zinc-800);
    font-size: 11px;
    overflow-x: auto;
  }
`;

// src/panel/styles/automations.css.js
var automationsStyles = i`
  /* Automations list (table-like rows) */
  .automations-list {
    border: 1px solid var(--selora-zinc-700);
    border-radius: 12px;
    overflow: hidden;
  }
  .auto-row {
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .auto-row:last-child {
    border-bottom: none;
  }
  .auto-row.disabled {
    opacity: 0.5;
  }
  .auto-row.highlighted {
    animation: highlightRow 3s ease;
  }
  .card.fading-out {
    animation: fadeOutCard 0.6s ease forwards;
    pointer-events: none;
  }
  .suggestions-section .automations-grid .card {
    animation: slideInCard 0.4s ease both;
  }
  .suggestions-section .automations-grid .card.fading-out {
    animation: fadeOutCard 0.6s ease forwards;
  }
  .auto-row-main {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .auto-row-main:hover {
    background: var(--secondary-background-color);
  }
  .auto-row-name {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .auto-row-title {
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .auto-row-desc {
    font-size: 12px;
    color: var(--secondary-text-color);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .auto-row-last-run {
    font-size: 12px;
    color: var(--secondary-text-color);
    white-space: nowrap;
    flex-shrink: 0;
    position: relative;
    cursor: default;
  }
  .auto-row-last-run .setting-tooltip {
    display: none;
    position: absolute;
    bottom: calc(100% + 8px);
    right: 0;
    left: auto;
    transform: none;
    width: auto;
    white-space: nowrap;
    padding: 10px 12px;
    background: var(--card-background-color, #1e1e1e);
    color: var(--primary-text-color);
    font-size: 12px;
    font-weight: 400;
    line-height: 1.5;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    z-index: 10;
    pointer-events: none;
  }
  .auto-row-last-run .setting-tooltip::after {
    content: "";
    position: absolute;
    top: 100%;
    left: auto;
    right: 12px;
    transform: none;
    border: 6px solid transparent;
    border-top-color: var(--card-background-color, #1e1e1e);
  }
  .auto-row-last-run:hover .setting-tooltip {
    display: block;
  }
  .auto-row-expand {
    padding: 0 16px 16px;
  }
  .last-run-prefix {
    display: none;
  }
  .auto-row-mobile-meta {
    display: none;
  }
  @media (max-width: 600px) {
    .auto-row-main {
      align-items: flex-start;
    }
    .auto-row-title {
      white-space: normal;
    }
    .auto-row-desc {
      white-space: normal;
    }
    .auto-row-last-run {
      display: none;
    }
    .auto-row-mobile-meta {
      display: flex;
      align-items: center;
      font-size: 11px;
      opacity: 0.45;
      color: var(--secondary-text-color);
      margin-top: 6px;
    }
    .auto-row-mobile-meta .card-chevron {
      position: absolute;
      right: 16px;
      bottom: 12px;
    }
    .auto-row-main {
      position: relative;
      padding-bottom: 8px;
    }
  }
  .filter-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  @media (max-width: 600px) {
    .filter-row {
      gap: 8px;
    }
    .filter-row .filter-input-wrap {
      flex: 1 1 100% !important;
    }
    .filter-row .status-pills {
      flex: 1;
    }
    .filter-row .sort-select {
      flex: 1;
    }
    .automations-summary span:first-child {
      display: none;
    }
  }
  .status-pills {
    display: inline-flex;
    gap: 2px;
    background: var(--selora-zinc-900);
    border: 1px solid var(--selora-zinc-700);
    border-radius: 8px;
    padding: 2px;
  }
  .status-pill {
    padding: 4px 12px;
    border: none;
    background: transparent;
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    color: var(--secondary-text-color);
    cursor: pointer;
    border-radius: 6px;
    transition: all 0.2s ease;
  }
  .status-pill:hover {
    color: var(--primary-text-color);
    background: var(--secondary-background-color);
  }
  .status-pill.active {
    background: var(--selora-zinc-700);
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .sort-select {
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    padding: 6px 10px;
    border-radius: 8px;
    border: 1px solid var(--selora-zinc-700);
    background: var(--selora-zinc-900);
    color: var(--primary-text-color);
    cursor: pointer;
    transition: border-color 0.3s;
  }
  .sort-select:hover {
    border-color: rgba(251, 191, 36, 0.5);
  }
  .automations-summary {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-bottom: 12px;
  }
  .filter-input-wrap {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--selora-inner-card-bg);
    border: 1px solid var(--selora-inner-card-border);
    border-radius: 10px;
    padding: 6px 12px;
    flex: 0 1 400px;
    transition: border-color 0.3s;
  }
  .filter-input-wrap:focus-within {
    border-color: var(--selora-accent);
  }
  .filter-input-wrap input {
    border: none;
    background: transparent;
    color: var(--primary-text-color);
    font-size: 13px;
    font-family: inherit;
    outline: none;
    flex: 1;
    min-width: 0;
  }
  .filter-input-wrap ha-icon {
    --mdc-icon-size: 16px;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  .bulk-select-all {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .bulk-select-all input {
    width: 16px;
    height: 16px;
    margin: 0;
    accent-color: var(--selora-accent);
    cursor: pointer;
  }
  .bulk-actions-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin: -2px 0 12px;
    padding: 8px 10px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--secondary-background-color);
  }
  .bulk-actions-row .left {
    font-size: 12px;
    font-weight: 600;
  }
  .bulk-actions-row .actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .card-select {
    display: inline-flex;
    align-items: center;
    margin-right: 6px;
  }
  .card-select input {
    width: 16px;
    height: 16px;
    margin: 0;
    accent-color: var(--selora-accent);
    cursor: pointer;
  }

  /* ---- Automations grid (flex columns for independent heights) ---- */
  .automations-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    align-items: stretch;
    gap: 20px;
    margin-bottom: 16px;
  }
  .automations-grid .masonry-col {
    display: contents;
  }
  @media (max-width: 900px) {
    .automations-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }
  @media (max-width: 600px) {
    .automations-grid {
      grid-template-columns: 1fr;
    }
  }
  .pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 12px 0;
  }
  .page-info {
    font-size: 12px;
    opacity: 0.6;
  }
  .per-page-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    color: var(--secondary-text-color);
  }
  .per-page-select {
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    padding: 6px 10px;
    border-radius: 10px;
    border: 1px solid var(--selora-zinc-700);
    background: transparent;
    color: var(--primary-text-color);
    cursor: pointer;
    transition: border-color 0.3s;
  }
  .per-page-select:hover {
    border-color: rgba(251, 191, 36, 0.5);
  }
  .automations-grid .card {
    margin-bottom: 0;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .automations-grid .card-header {
    margin-bottom: 0;
    align-items: center;
  }
  .automations-grid .card h3 {
    font-size: 13px;
    line-height: 1.3;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .automations-grid .card-meta {
    font-size: 11px;
    color: var(--secondary-text-color);
    opacity: 0.7;
  }

  /* ---- Automation detail drawer (below grid) ---- */
  .automation-detail-drawer {
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 14px;
    box-shadow: var(--card-box-shadow);
  }
  .automation-detail-drawer .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .automation-detail-drawer .detail-header h3 {
    margin: 0;
    font-size: 16px;
  }
`;

// src/panel/styles/settings.css.js
var settingsStyles = i`
  .settings-form {
    max-width: 640px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .settings-section {
    /* inherits from .section-card */
  }
  .settings-section-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--secondary-text-color);
    margin: 0 0 16px;
  }
  .form-group {
    margin-bottom: 18px;
  }
  .form-group:last-child {
    margin-bottom: 0;
  }
  .form-group label {
    display: block;
    margin-bottom: 6px;
    font-weight: 500;
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .form-select {
    width: 100%;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--selora-zinc-900);
    color: var(--primary-text-color);
    border: 1px solid var(--selora-zinc-700);
    font-size: 14px;
    appearance: none;
    -webkit-appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23a1a1aa' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .form-select:focus {
    outline: none;
    border-color: var(--selora-accent);
  }
  .key-hint {
    font-size: 12px;
    color: var(--selora-zinc-400);
    font-family: monospace;
    padding: 6px 10px;
    background: var(--selora-zinc-900);
    border: 1px solid var(--selora-zinc-700);
    border-radius: 8px;
    display: inline-block;
    margin-top: 4px;
  }
  .key-not-set {
    font-size: 12px;
    color: var(--selora-zinc-400);
    font-style: italic;
    margin-top: 4px;
  }
  .service-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 0;
  }
  .service-row:not(:last-child) {
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .service-row label {
    font-size: 14px;
    font-weight: 500;
    color: var(--primary-text-color);
    flex: 1;
  }
  .service-details {
    padding: 16px 0 0 0;
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .advanced-section {
    padding: 0;
    overflow: hidden;
  }
  .advanced-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    font-size: 15px;
    font-weight: 500;
    color: var(--primary-text-color);
    list-style: none;
    padding: 16px 20px;
    transition: background 0.15s;
  }
  .advanced-toggle::-webkit-details-marker {
    display: none;
  }
  .advanced-toggle::marker {
    display: none;
    content: "";
  }
  .advanced-toggle:hover {
    background: var(--secondary-background-color);
  }
  .advanced-chevron {
    --mdc-icon-size: 18px;
    transition: transform 0.2s;
    opacity: 0.5;
  }
  .advanced-section[open] > .advanced-toggle .advanced-chevron {
    transform: rotate(90deg);
  }
  .advanced-section[open] > .advanced-toggle {
    border-bottom: 1px solid var(--divider-color);
  }
  .advanced-section .service-row:first-of-type {
    padding-top: 16px;
  }
  .advanced-section .service-row,
  .advanced-section .service-details,
  .advanced-section .settings-section-title,
  .advanced-section .settings-separator {
    margin-left: 20px;
    margin-right: 20px;
  }
  .advanced-section .service-row:last-of-type {
    padding-bottom: 16px;
  }
  .settings-form ha-switch {
    --switch-checked-color: var(--selora-accent);
    --switch-checked-button-color: var(--selora-accent);
    --switch-checked-track-color: var(--selora-accent-dark);
    --mdc-theme-secondary: var(--selora-accent);
  }
  .service-row label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .setting-help {
    position: relative;
    cursor: help;
    --mdc-icon-size: 16px;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  .setting-help:hover {
    color: var(--primary-text-color);
  }
  .setting-help .setting-tooltip {
    display: none;
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    width: 240px;
    padding: 10px 12px;
    background: var(--card-background-color, #1e1e1e);
    color: var(--primary-text-color);
    font-size: 12px;
    font-weight: 400;
    line-height: 1.5;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    z-index: 10;
    pointer-events: none;
  }
  .setting-help .setting-tooltip::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 6px solid transparent;
    border-top-color: var(--card-background-color, #1e1e1e);
  }
  .setting-help:hover .setting-tooltip {
    display: block;
  }
  .settings-separator {
    border: none;
    border-top: 1px solid var(--selora-zinc-700);
    margin: 16px 0 4px;
  }
  .save-bar {
    display: flex;
    justify-content: flex-end;
  }
`;

// src/panel/styles/index.css.js
var allPanelStyles = [
  layoutStyles,
  sidebarStyles,
  headerStyles,
  chatStyles,
  proposalStyles,
  cardElementStyles,
  automationsStyles,
  settingsStyles,
];

// src/shared/date-utils.js
function relativeTime(date) {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1e3);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return date.toLocaleDateString();
}
function formatDate(iso) {
  if (!iso) return "";
  try {
    const d3 = new Date(iso);
    const diffMs = Date.now() - d3.getTime();
    if (diffMs < 0) return "";
    if (diffMs < 6e4) return "just now";
    if (diffMs < 36e5) return `${Math.floor(diffMs / 6e4)}m ago`;
    if (diffMs < 864e5) return `${Math.floor(diffMs / 36e5)}h ago`;
    return d3.toLocaleDateString();
  } catch {
    return "";
  }
}
function formatTimeAgo(iso) {
  if (!iso) return null;
  try {
    const diff = Date.now() - new Date(iso).getTime();
    if (diff < 0) return null;
    const mins = Math.floor(diff / 6e4);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  } catch {
    return null;
  }
}
function formatTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

// node_modules/lit-html/directive.js
var e4 =
  (t3) =>
  (...e5) => ({ _$litDirective$: t3, values: e5 });
var i3 = class {
  constructor(t3) {}
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AT(t3, e5, i5) {
    ((this._$Ct = t3), (this._$AM = e5), (this._$Ci = i5));
  }
  _$AS(t3, e5) {
    return this.update(t3, e5);
  }
  update(t3, e5) {
    return this.render(...e5);
  }
};

// node_modules/lit-html/directive-helpers.js
var { I: l4 } = j;
var s5 = {};
var a3 = (o5, l5 = s5) => (o5._$AH = l5);

// node_modules/lit-html/directives/keyed.js
var i4 = e4(
  class extends i3 {
    constructor() {
      (super(...arguments), (this.key = A));
    }
    render(r4, t3) {
      return ((this.key = r4), t3);
    }
    update(r4, [t3, e5]) {
      return (t3 !== this.key && (a3(r4), (this.key = t3)), e5);
    }
  },
);

// src/shared/markdown.js
function stripAutomationBlock(text) {
  if (!text)
    return { text: "", hasAutomationBlock: false, isPartialBlock: false };
  const completeRe = /```automation[\s\S]*?```/g;
  const hasComplete = completeRe.test(text);
  let cleaned = text.replace(completeRe, "").trim();
  const partialRe = /```automation[\s\S]*$/;
  const hasPartial = !hasComplete && partialRe.test(cleaned);
  if (hasPartial) {
    cleaned = cleaned.replace(partialRe, "").trim();
  }
  return {
    text: cleaned,
    hasAutomationBlock: hasComplete,
    isPartialBlock: hasPartial,
  };
}
function renderMarkdown(text) {
  if (!text) return "";
  let escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  escaped = escaped.replace(
    /```([\s\S]*?)```/g,
    '<pre style="background:var(--primary-background-color,#18181b);color:var(--primary-text-color,#e4e4e7);padding:10px;border-radius:8px;border:1px solid var(--divider-color,#27272a);font-size:12px;overflow-x:auto;margin:8px 0;">$1</pre>',
  );
  escaped = escaped.replace(
    /`([^`]+)`/g,
    '<code style="background:var(--secondary-background-color,rgba(255,255,255,0.08));padding:2px 5px;border-radius:4px;font-size:13px;border:1px solid var(--divider-color,rgba(255,255,255,0.06));">$1</code>',
  );
  escaped = escaped.replace(
    /^####\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:14px;margin:10px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^###\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:15px;margin:12px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^##\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:16px;margin:14px 0 6px;">$1</div>',
  );
  escaped = escaped.replace(
    /^#\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:17px;margin:16px 0 6px;">$1</div>',
  );
  escaped = escaped.replace(
    /\*\*(.+?)\*\*/g,
    '<strong style="color:var(--selora-accent-text,#fbbf24);">$1</strong>',
  );
  escaped = escaped.replace(
    /(?<!\w)\*([^\s*](?:.*?[^\s*])?)\*(?!\w)/g,
    "<em>$1</em>",
  );
  escaped = escaped.replace(
    /(?<![a-zA-Z0-9_])_([^\s_](?:.*?[^\s_])?)_(?![a-zA-Z0-9_])/g,
    "<em>$1</em>",
  );
  escaped = escaped.replace(
    /^(\d+)\.\s+(.+)$/gm,
    '<div style="display:flex;gap:6px;margin:2px 0 2px 4px;align-items:baseline;"><span style="opacity:0.55;flex-shrink:0;min-width:18px;">$1.</span><span style="flex:1;">$2</span></div>',
  );
  escaped = escaped.replace(
    /^[-•]\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(251,191,36,0.35);">$1</div>',
  );
  escaped = escaped.replace(/\n/g, "<br>");
  return escaped;
}

// src/panel/render-chat.js
function renderNewAutomationDialog(host) {
  if (!host._showNewAutoDialog) return "";
  return x`
    <div
      class="modal-overlay"
      @click=${() => {
        host._showNewAutoDialog = false;
      }}
    >
      <div
        class="modal-content"
        style="max-width:420px;"
        @click=${(e5) => e5.stopPropagation()}
      >
        <h3 style="margin:0 0 16px;">New Automation</h3>
        <label
          style="font-size:13px;font-weight:500;display:block;margin-bottom:6px;"
          >Automation name</label
        >
        <div style="display:flex;gap:8px;align-items:center;">
          <input
            type="text"
            placeholder="e.g. Turn off lights at midnight"
            style="flex:1;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;font-size:14px;background:var(--card-background-color);color:var(--primary-text-color);box-sizing:border-box;"
            .value=${host._newAutoName}
            @input=${(e5) => {
              host._newAutoName = e5.target.value;
            }}
            @keydown=${(e5) => {
              if (e5.key === "Enter")
                host._newAutomationChat(host._newAutoName);
            }}
          />
          <button
            class="btn btn-outline"
            style="padding:8px 10px;flex-shrink:0;"
            title="AI Suggest"
            ?disabled=${host._suggestingName}
            @click=${() => host._suggestAutomationName()}
          >
            ${
              host._suggestingName
                ? x`<span class="spinner green"></span>`
                : x`<ha-icon
                  icon="mdi:auto-fix"
                  style="--mdc-icon-size:18px;"
                ></ha-icon>`
            }
          </button>
        </div>
        ${
          host._suggestingName
            ? x`<div
              style="font-size:12px;color:var(--secondary-text-color);margin-top:6px;"
            >
              Asking AI for a suggestion…
            </div>`
            : ""
        }
        <div
          style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;"
        >
          <button
            class="btn btn-outline"
            @click=${() => {
              host._showNewAutoDialog = false;
            }}
          >
            Cancel
          </button>
          <button
            class="btn btn-primary"
            ?disabled=${!host._newAutoName?.trim()}
            @click=${() => host._newAutomationChat(host._newAutoName)}
          >
            <ha-icon
              icon="mdi:chat-processing-outline"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            Create in Chat
          </button>
        </div>
      </div>
    </div>
  `;
}
function renderChat(host) {
  return x`
    <div class="chat-pane">
      <div class="chat-messages" id="chat-messages">
        ${
          host._messages.length === 0
            ? i4(
                host._welcomeKey || 0,
                x`
                <div
                  class="empty-state welcome"
                  style="max-width:560px;margin:0 auto;padding:24px;"
                >
                  <div class="section-card" style="text-align:center;">
                    <img
                      src="/api/selora_ai/logo.png"
                      alt="Selora AI"
                      style="width:56px;height:56px;border-radius:12px;margin-bottom:12px;"
                    />
                    <div
                      style="font-size:20px;font-weight:700;margin-bottom:6px;"
                    >
                      Welcome to
                      <span class="gold-text">Selora AI</span>
                    </div>
                    <div class="section-card-subtitle">
                      Your intelligent home automation architect. I analyze your
                      devices, detect patterns, and help you build automations
                      using natural language.
                    </div>
                    <div
                      style="display:grid;grid-template-columns:1fr 1fr;gap:12px;text-align:left;margin-bottom:24px;"
                    >
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() => host._quickStart("Create an automation for my home")}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:lightning-bolt"
                            style="--mdc-icon-size:18px;color:#fbbf24;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Create Automations
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          Describe what you want in plain English
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() =>
                          host._quickStart(
                            "Analyze my device usage patterns and suggest automations",
                          )}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:magnify-scan"
                            style="--mdc-icon-size:18px;color:#3b82f6;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Detect Patterns
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          AI spots your routines and suggests automations
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() =>
                          host._quickStart(
                            "What devices do I have and how are they organized?",
                          )}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:home-search-outline"
                            style="--mdc-icon-size:18px;color:#22c55e;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Manage Devices
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          Discover, organize, and control your smart home
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() => host._quickStart("What can you help me with?")}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:chat-question-outline"
                            style="--mdc-icon-size:18px;color:#a855f7;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Ask Anything
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          Get answers about your home setup
                        </div>
                      </div>
                    </div>
                    <div
                      class="section-card-subtitle"
                      style="margin-bottom:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;opacity:0.4;"
                    >
                      Quick start
                    </div>
                    <div
                      style="display:flex;flex-direction:column;gap:8px;width:100%;"
                    >
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:12px 16px;font-size:13px;"
                        @click=${() =>
                          host._quickStart(
                            "Create an automation that turns off all lights at midnight",
                          )}
                      >
                        <ha-icon
                          icon="mdi:lightbulb-off-outline"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        Turn off all lights at midnight
                      </button>
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:12px 16px;font-size:13px;"
                        @click=${() =>
                          host._quickStart(
                            "What devices do I have and which ones are currently on?",
                          )}
                      >
                        <ha-icon
                          icon="mdi:devices"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        What devices do I have?
                      </button>
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:12px 16px;font-size:13px;"
                        @click=${() =>
                          host._quickStart(
                            "Suggest useful automations based on my devices and usage patterns",
                          )}
                      >
                        <ha-icon
                          icon="mdi:auto-fix"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        Suggest automations for my home
                      </button>
                    </div>
                  </div>
                </div>
              `,
              )
            : host._messages.map((msg, idx) => renderMessage(host, msg, idx))
        }
        ${
          host._loading
            ? x`
              <div class="typing-bubble">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
              </div>
            `
            : ""
        }
      </div>

      <div class="chat-input-wrapper">
        <div class="chat-input">
          <ha-textfield
            .value=${host._input}
            @input=${(e5) => (host._input = e5.target.value)}
            @keydown=${(e5) => e5.key === "Enter" && !e5.shiftKey && host._sendMessage()}
            placeholder="Describe an automation or ask a question…"
            ?disabled=${host._loading || host._streaming}
            style="flex:1;"
          ></ha-textfield>
          ${
            host._streaming
              ? x` <ha-icon-button
                @click=${() => host._stopStreaming()}
                title="Stop generating"
                style="color:#fbbf24;"
              >
                <ha-icon icon="mdi:stop-circle"></ha-icon>
              </ha-icon-button>`
              : x` <ha-icon-button
                @click=${() => host._sendMessage()}
                ?disabled=${host._loading || !host._input.trim()}
                title="Send"
              >
                <ha-icon icon="mdi:send"></ha-icon>
              </ha-icon-button>`
          }
        </div>
      </div>
    </div>
  `;
}
function renderMessage(host, msg, idx) {
  const isUser = msg.role === "user";
  if (msg._streaming && !msg.content) return x``;
  let displayContent = msg.content;
  let showAutomationSpinner = false;
  if (!isUser) {
    const { text, isPartialBlock } = stripAutomationBlock(msg.content);
    displayContent = text;
    showAutomationSpinner = isPartialBlock && msg._streaming;
  }
  return x`
    <div class="message-row">
      ${
        isUser
          ? x`
            <div class="bubble user">
              <span class="msg-content" .innerHTML=${msg.content}></span>
            </div>
          `
          : x`
            <div
              style="display:inline-flex;flex-direction:column;max-width:82%;align-self:flex-start;"
            >
              <div
                class="bubble assistant"
                style="max-width:100%;align-self:auto;"
              >
                <span
                  class="msg-content ${msg._streaming ? "streaming-cursor" : ""}"
                  .innerHTML=${renderMarkdown(displayContent)}
                ></span>
                ${
                  showAutomationSpinner
                    ? x`
                      <div
                        style="display:flex;align-items:center;gap:10px;margin-top:12px;padding:12px;border-radius:8px;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.15);"
                      >
                        <div
                          class="typing-dot"
                          style="animation:blink 1s infinite;width:8px;height:8px;border-radius:50%;background:#fbbf24;"
                        ></div>
                        <span
                          style="font-size:13px;font-weight:500;color:#fbbf24;"
                          >Building automation...</span
                        >
                      </div>
                    `
                    : ""
                }
                ${
                  msg.config_issue
                    ? x`
                      <div style="margin-top: 10px;">
                        <mwc-button dense raised @click=${host._goToSettings}
                          >Go to Settings</mwc-button
                        >
                      </div>
                    `
                    : ""
                }
                ${msg.automation ? host._renderProposalCard(msg, idx) : ""}
              </div>
              <div
                class="bubble-meta"
                style="display:flex;justify-content:space-between;align-items:center;width:100%;"
              >
                <span>Selora AI · ${formatTime(msg.timestamp)}</span>
                <button
                  class="copy-msg-btn"
                  title="Copy message"
                  @click=${(e5) => host._copyMessageText(msg, e5.currentTarget)}
                >
                  <ha-icon
                    icon="mdi:content-copy"
                    style="--mdc-icon-size:12px;"
                  ></ha-icon>
                </button>
              </div>
            </div>
          `
      }
      ${
        isUser
          ? x` <div class="bubble-meta">
            You · ${formatTime(msg.timestamp)}
          </div>`
          : ""
      }
    </div>
  `;
}
function renderYamlEditor(host, key, originalYaml, onSave = null) {
  host._initYamlEdit(key, originalYaml);
  const current = host._editedYaml[key] ?? originalYaml;
  const isDirty = current !== originalYaml;
  const saving = !!host._savingYaml[key];
  return x`
    <ha-code-editor
      mode="yaml"
      .value=${current}
      @value-changed=${(e5) => {
        host._onYamlInput(key, e5.detail.value);
      }}
      autocomplete-entities
      style="--code-mirror-font-size:12px;"
    ></ha-code-editor>
    ${
      isDirty || onSave
        ? x`
          <div class="yaml-edit-bar">
            ${
              isDirty
                ? x`
                  <span class="yaml-unsaved">
                    <ha-icon
                      icon="mdi:circle-edit-outline"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    Unsaved changes
                  </span>
                `
                : x`<span style="flex:1;"></span>`
            }
            ${
              onSave
                ? x`
                  <button
                    class="btn btn-primary"
                    ?disabled=${saving || !isDirty}
                    @click=${() => onSave(key)}
                  >
                    <ha-icon
                      icon="mdi:content-save"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    ${saving ? "Saving\u2026" : "Save changes"}
                  </button>
                `
                : ""
            }
          </div>
        `
        : ""
    }
  `;
}

// src/shared/formatting.js
function humanizeToken(value) {
  if (value == null || value === "") return "";
  return String(value)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c3) => c3.toUpperCase());
}
function fmtEntity(hass, id) {
  if (!id) return "";
  const eid = String(id);
  const stateObj = hass?.states?.[eid];
  if (stateObj?.attributes?.friendly_name)
    return stateObj.attributes.friendly_name;
  const parts = eid.split(".");
  const raw = (parts.length > 1 ? parts.slice(1).join(".") : parts[0]).replace(
    /_/g,
    " ",
  );
  return raw.replace(/\b\w/g, (c3) => c3.toUpperCase());
}
function fmtEntities(hass, val) {
  if (!val) return "";
  const arr = Array.isArray(val) ? val : [val];
  if (arr.length === 1) return fmtEntity(hass, arr[0]);
  if (arr.length === 2)
    return `${fmtEntity(hass, arr[0])} and ${fmtEntity(hass, arr[1])}`;
  return (
    arr
      .slice(0, -1)
      .map((e5) => fmtEntity(hass, e5))
      .join(", ") +
    ", and " +
    fmtEntity(hass, arr[arr.length - 1])
  );
}
function fmtState(state) {
  if (state == null) return null;
  const s6 = String(state);
  const friendly = {
    on: "on",
    off: "off",
    home: "home",
    not_home: "away",
    open: "open",
    closed: "closed",
    locked: "locked",
    unlocked: "unlocked",
    playing: "playing",
    paused: "paused",
    idle: "idle",
    unavailable: "unavailable",
    unknown: "unknown",
  };
  return friendly[s6] || s6.replace(/_/g, " ");
}
function fmtDuration(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  const parts = [
    value.hours ? `${value.hours}h` : "",
    value.minutes ? `${value.minutes}m` : "",
    value.seconds ? `${value.seconds}s` : "",
  ].filter(Boolean);
  if (parts.length) return parts.join(" ");
  return String(value);
}
function fmtWeekdays(value) {
  if (!value) return "";
  const dayMap = {
    mon: "Mon",
    tue: "Tue",
    wed: "Wed",
    thu: "Thu",
    fri: "Fri",
    sat: "Sat",
    sun: "Sun",
  };
  const days = Array.isArray(value) ? value : [value];
  return days.map((d3) => dayMap[String(d3)] || humanizeToken(d3)).join(", ");
}
function fmtNumericValue(entityId, value) {
  if (value == null || value === "") return "";
  const raw = String(value).trim();
  const batteryLike = String(entityId || "")
    .toLowerCase()
    .includes("battery");
  if (batteryLike && /^-?\d+(\.\d+)?$/.test(raw) && !raw.includes("%")) {
    return `${raw}%`;
  }
  return raw;
}
function fmtTime(hass, val) {
  if (val == null) return String(val);
  const s6 = String(val).trim();
  if (s6.includes("{{") || s6.includes("{%")) {
    const m2 = s6.match(/states\(['"]([^'"]+)['"]\)/);
    if (m2) return fmtEntity(hass, m2[1]);
    const m22 = s6.match(/state_attr\(['"]([^'"]+)['"]/);
    if (m22) return fmtEntity(hass, m22[1]);
    return "a calculated time";
  }
  const num = Number(s6);
  if (!isNaN(num) && num >= 0 && num <= 86400 && !s6.includes(":")) {
    const h3 = Math.floor(num / 3600);
    const m2 = Math.floor((num % 3600) / 60);
    const ampm = h3 >= 12 ? "PM" : "AM";
    const h12 = h3 === 0 ? 12 : h3 > 12 ? h3 - 12 : h3;
    return `${h12}:${String(m2).padStart(2, "0")} ${ampm}`;
  }
  const parts = s6.split(":");
  if (parts.length >= 2) {
    const h3 = parseInt(parts[0], 10);
    const m2 = parseInt(parts[1], 10);
    if (!isNaN(h3) && !isNaN(m2)) {
      const ampm = h3 >= 12 ? "PM" : "AM";
      const h12 = h3 === 0 ? 12 : h3 > 12 ? h3 - 12 : h3;
      return `${h12}:${String(m2).padStart(2, "0")} ${ampm}`;
    }
  }
  if (s6.startsWith("input_datetime.") || s6.startsWith("sensor."))
    return fmtEntity(hass, s6);
  return s6;
}

// src/shared/flow-description.js
function describeFlowItem(hass, item) {
  if (!item || typeof item !== "object") return String(item ?? "");
  const p2 = item.platform || item.trigger;
  if (p2 === "time") {
    const raw = item.at;
    if (Array.isArray(raw)) {
      return `When the time is ${raw.map((t3) => fmtTime(hass, t3)).join(" or ")}`;
    }
    return `When the time is ${fmtTime(hass, raw)}`;
  }
  if (p2 === "sun") {
    const ev =
      item.event === "sunset"
        ? "sunset"
        : item.event === "sunrise"
          ? "sunrise"
          : humanizeToken(item.event || "sun event").toLowerCase();
    const offset = item.offset ? ` (${item.offset})` : "";
    return `When it is ${ev}${offset}`;
  }
  if (p2 === "state") {
    const eid = fmtEntities(hass, item.entity_id);
    const fromState = fmtState(item.from);
    const toState = fmtState(item.to);
    const duration = fmtDuration(item.for);
    const dur = duration ? ` for ${duration}` : "";
    if (toState === "on") return `When ${eid} turns on${dur}`;
    if (toState === "off") return `When ${eid} turns off${dur}`;
    if (toState && fromState)
      return `When ${eid} changes from ${fromState} to ${toState}${dur}`;
    if (toState) return `When ${eid} becomes ${toState}${dur}`;
    return `When ${eid} changes state${dur}`;
  }
  if (p2 === "numeric_state") {
    const eid = fmtEntities(hass, item.entity_id);
    const above = fmtNumericValue(item.entity_id, item.above);
    const below = fmtNumericValue(item.entity_id, item.below);
    if (item.above != null && item.below != null)
      return `When ${eid} is between ${above} and ${below}`;
    if (item.above != null) return `When ${eid} rises above ${above}`;
    if (item.below != null) return `When ${eid} drops below ${below}`;
    return `When ${eid} value changes`;
  }
  if (p2 === "homeassistant") {
    const ev =
      item.event === "start"
        ? "starts"
        : item.event === "shutdown"
          ? "shuts down"
          : "changes state";
    return `When Home Assistant ${ev}`;
  }
  if (p2 === "time_pattern") {
    if (item.seconds != null)
      return `Every ${item.seconds} second${Number(item.seconds) === 1 ? "" : "s"}`;
    if (item.minutes != null)
      return `Every ${item.minutes} minute${Number(item.minutes) === 1 ? "" : "s"}`;
    if (item.hours != null)
      return `Every ${item.hours} hour${Number(item.hours) === 1 ? "" : "s"}`;
    return "On a time pattern";
  }
  if (p2 === "template") {
    const tmpl = item.value_template || "";
    const entityMatch = tmpl.match(/states\(['"]([^'"]+)['"]\)/);
    if (entityMatch)
      return `When ${fmtEntity(hass, entityMatch[1])} condition is met`;
    return "When a template condition is met";
  }
  if (p2 === "event") {
    const name = item.event_type
      ? humanizeToken(item.event_type).toLowerCase()
      : "an event";
    return `When ${name} happens`;
  }
  if (p2 === "device") {
    const triggerType = item.type
      ? humanizeToken(item.type).toLowerCase()
      : "triggered";
    return item.device_id
      ? `When a device ${triggerType}`
      : `When a device is ${triggerType}`;
  }
  if (p2 === "zone") {
    const eid = fmtEntities(hass, item.entity_id);
    const zone = fmtEntity(hass, item.zone);
    const eventMap = {
      enter: "enters",
      leave: "leaves",
    };
    const rawEvent = String(item.event || "enter");
    const ev = eventMap[rawEvent] || humanizeToken(rawEvent).toLowerCase();
    return `${eid} ${ev} ${zone}`.trim();
  }
  if (p2 === "mqtt")
    return item.topic
      ? `When a device message arrives (${item.topic})`
      : "When a device message arrives";
  if (p2 === "webhook") return "When an outside service sends an update";
  if (p2 === "tag")
    return `When a tag is scanned${item.tag_id ? ` (${item.tag_id})` : ""}`;
  if (p2 === "geo_location") return "When a location update is received";
  if (p2 === "calendar") {
    const eventName = item.event
      ? humanizeToken(item.event).toLowerCase()
      : "event";
    const entity = item.entity_id
      ? ` on ${fmtEntity(hass, item.entity_id)}`
      : "";
    return `When a calendar ${eventName} begins${entity}`;
  }
  if (p2) return "When this trigger happens";
  const cond = item.condition;
  if (cond === "state") {
    const eid = fmtEntities(hass, item.entity_id);
    const st = fmtState(item.state ?? item.to);
    return `${eid} is ${st}`;
  }
  if (cond === "numeric_state") {
    const eid = fmtEntities(hass, item.entity_id);
    if (item.above != null && item.below != null)
      return `${eid} between ${item.above} and ${item.below}`;
    if (item.above != null) return `${eid} above ${item.above}`;
    if (item.below != null) return `${eid} below ${item.below}`;
    return `${eid} numeric check`;
  }
  if (cond === "time") {
    const parts = [];
    if (item.after) parts.push(`after ${fmtTime(hass, item.after)}`);
    if (item.before) parts.push(`before ${fmtTime(hass, item.before)}`);
    if (item.weekday) {
      parts.push(`on ${fmtWeekdays(item.weekday)}`);
    }
    return parts.length ? parts.join(" \xB7 ") : "Time window";
  }
  if (cond === "template") return "Template evaluates to true";
  if (cond === "sun") {
    const parts = [];
    if (item.after)
      parts.push(`after ${String(item.after).replace(/_/g, " ")}`);
    if (item.before)
      parts.push(`before ${String(item.before).replace(/_/g, " ")}`);
    return parts.join(", ") || "Sun position";
  }
  if (cond === "and")
    return `All ${(item.conditions || []).length} conditions must be true`;
  if (cond === "or")
    return `Any of ${(item.conditions || []).length} conditions is true`;
  if (cond === "not") return "None of the conditions are true";
  if (cond === "zone") {
    const eid = fmtEntities(hass, item.entity_id);
    return `${eid} is in ${fmtEntity(hass, item.zone) || "zone"}`;
  }
  if (cond === "device")
    return item.type
      ? String(item.type).replace(/_/g, " ")
      : "Device condition";
  if (cond) return String(cond).replace(/_/g, " ");
  const svc = item.service || item.action;
  if (svc) {
    const svcStr = String(svc);
    const [domain = "", svcName = svc] = svcStr.split(".");
    if (
      svcStr === "notify.persistent_notification" ||
      domain === "persistent_notification"
    ) {
      const title = item.data?.title;
      const msg = item.data?.message;
      if (title && msg) return `Notify: "${title}"`;
      if (title) return `Notify: "${title}"`;
      if (msg) {
        const short = msg.length > 60 ? msg.slice(0, 57) + "\u2026" : msg;
        return `Notify: "${short}"`;
      }
      return "Send a notification";
    }
    if (domain === "notify") {
      const target = svcName
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c3) => c3.toUpperCase());
      const msg = item.data?.message;
      const title = item.data?.title;
      if (title) return `Notify ${target}: "${title}"`;
      if (msg) {
        const short = msg.length > 50 ? msg.slice(0, 47) + "\u2026" : msg;
        return `Notify ${target}: "${short}"`;
      }
      return `Notify via ${target}`;
    }
    if (domain === "tts") {
      const msg = item.data?.message;
      if (msg) {
        const short = msg.length > 50 ? msg.slice(0, 47) + "\u2026" : msg;
        return `Say: "${short}"`;
      }
      return "Text-to-speech";
    }
    const friendlyActions = {
      turn_on: "Turn on",
      turn_off: "Turn off",
      toggle: "Toggle",
      lock: "Lock",
      unlock: "Unlock",
      open_cover: "Open",
      close_cover: "Close",
      set_temperature: "Set temperature for",
      set_value: "Set value for",
      send_command: "Send command to",
      reload: "Reload",
    };
    const name =
      friendlyActions[svcName] ||
      svcName.replace(/_/g, " ").replace(/\b\w/g, (c3) => c3.toUpperCase());
    const targets = item.target?.entity_id ?? item.data?.entity_id;
    const t3 = fmtEntities(hass, targets);
    const extras = [];
    if (item.data?.brightness_pct != null)
      extras.push(`at ${item.data.brightness_pct}%`);
    if (item.data?.temperature != null)
      extras.push(`to ${item.data.temperature}\xB0`);
    if (item.data?.color_temp != null)
      extras.push(`color temp ${item.data.color_temp}`);
    if (item.data?.message && !String(item.data.message).includes("{{")) {
      const short =
        item.data.message.length > 50
          ? item.data.message.slice(0, 47) + "\u2026"
          : item.data.message;
      extras.push(`"${short}"`);
    }
    if (item.data?.title && !String(item.data.title).includes("{{"))
      extras.push(item.data.title);
    const detail = extras.length ? ` (${extras.join(", ")})` : "";
    return t3 ? `${name} ${t3}${detail}` : `${name}${detail}`;
  }
  if (item.delay) {
    const d3 = item.delay;
    if (typeof d3 === "string") return `Wait ${d3}`;
    const parts = [];
    if (d3.hours) parts.push(`${d3.hours}h`);
    if (d3.minutes) parts.push(`${d3.minutes}m`);
    if (d3.seconds) parts.push(`${d3.seconds}s`);
    return parts.length ? `Wait ${parts.join(" ")}` : "Wait";
  }
  if (item.wait_template) return "Wait until condition is met";
  if (item.wait_for_trigger) return "Wait for a trigger";
  if (item.scene) return `Activate scene: ${fmtEntity(hass, item.scene)}`;
  if (item.choose)
    return `Choose between ${item.choose.length} option${item.choose.length !== 1 ? "s" : ""}`;
  if (item.repeat) {
    const r4 = item.repeat;
    if (r4.count != null)
      return `Repeat ${r4.count} time${r4.count !== 1 ? "s" : ""}`;
    if (r4.while) return "Repeat while condition holds";
    if (r4.until) return "Repeat until condition is met";
    return "Repeat";
  }
  if (item.parallel)
    return `Run ${(item.parallel || []).length} actions in parallel`;
  if (item.sequence)
    return `Run a sequence of ${(item.sequence || []).length} steps`;
  if (item.variables) return "Set variables";
  if (item.stop) return `Stop: ${item.stop}`;
  if (item.event) return `Fire event: ${String(item.event).replace(/_/g, " ")}`;
  const SKIP = /* @__PURE__ */ new Set([
    "id",
    "enabled",
    "mode",
    "alias",
    "description",
  ]);
  const readable = Object.entries(item)
    .filter(([k2, v2]) => !SKIP.has(k2) && v2 != null && v2 !== "")
    .map(([k2, v2]) => {
      const label = k2.replace(/_/g, " ");
      const strVal =
        typeof v2 === "string"
          ? v2
          : Array.isArray(v2)
            ? v2
                .map((x2) => (typeof x2 === "object" ? "\u2026" : x2))
                .join(", ")
            : String(v2);
      if (strVal.includes("{{") || strVal.includes("{%")) return null;
      return `${label}: ${strVal}`;
    })
    .filter(Boolean)
    .slice(0, 3);
  return readable.length ? readable.join(" \xB7 ") : "Automation step";
}

// src/panel/render-suggestions.js
var MIN_CONF = 0.8;
var COLLAPSED_COUNT = 3;
function normalizeProactive(s6) {
  return {
    type: "proactive",
    cardKey: `proactive_${s6.suggestion_id}`,
    title: s6.description,
    subtitle: s6.evidence_summary || null,
    risk: null,
    automationYaml: s6.automation_yaml || "",
    automationData: s6.automation_data || null,
    _original: s6,
    _suggestionId: s6.suggestion_id,
  };
}
function normalizeLLM(item) {
  const auto = item.automation || item.automation_data;
  return {
    type: "llm",
    cardKey: `sug_${auto.alias}`,
    title: auto.alias,
    subtitle: auto.description || null,
    risk: item.risk_assessment || auto?.risk_assessment || null,
    automationYaml: item.automation_yaml || "",
    automationData: auto,
    _original: item,
    _auto: auto,
  };
}
function buildQualified(host) {
  const seenKeys = /* @__PURE__ */ new Set();
  const qualified = [];
  for (const s6 of host._proactiveSuggestions || []) {
    if ((s6.confidence || 0) < MIN_CONF) continue;
    const key = (s6.description || "").toLowerCase().trim();
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    qualified.push(normalizeProactive(s6));
  }
  for (const item of host._suggestions || []) {
    const auto = item.automation || item.automation_data;
    if (!auto) continue;
    const key = (auto.alias || "").toLowerCase().trim();
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    qualified.push(normalizeLLM(item));
  }
  return qualified;
}
function applyFilters(host, qualified) {
  const filterText = (host._suggestionFilter || "").toLowerCase().trim();
  const sourceFilter = host._suggestionSourceFilter || "all";
  const sortBy = host._suggestionSortBy || "recent";
  const filtered = qualified.filter((item) => {
    if (filterText) {
      const text = `${item.title} ${item.subtitle || ""}`.toLowerCase();
      if (!text.includes(filterText)) return false;
    }
    if (sourceFilter === "pattern" && item.type !== "proactive") return false;
    if (sourceFilter === "ai" && item.type !== "llm") return false;
    return true;
  });
  if (sortBy === "alpha") {
    filtered.sort((a4, b2) => (a4.title || "").localeCompare(b2.title || ""));
  } else {
    filtered.sort((a4, b2) => {
      if (a4.type !== b2.type) return a4.type === "llm" ? -1 : 1;
      const confA = a4._original?.confidence || 0;
      const confB = b2._original?.confidence || 0;
      return confB - confA;
    });
  }
  return filtered;
}
function renderSuggestionCard(host, item, bulkMode = false, selectedKeys = {}) {
  const { cardKey, automationData } = item;
  const editedYaml = host._editedYaml[cardKey];
  const displayYaml = editedYaml !== void 0 ? editedYaml : item.automationYaml;
  const hasFlow =
    automationData &&
    (automationData.trigger?.length ||
      automationData.triggers?.length ||
      automationData.action?.length ||
      automationData.actions?.length);
  const activeTab =
    host._cardActiveTab[cardKey] !== void 0
      ? host._cardActiveTab[cardKey]
      : null;
  const isProactive = item.type === "proactive";
  const accepting = isProactive
    ? !!host._acceptingProactive[item._suggestionId]
    : !!host._savingYaml[cardKey];
  const dismissing = isProactive
    ? !!host._dismissingProactive[item._suggestionId]
    : false;
  const fadingOut = !!(host._fadingOutSuggestions || {})[cardKey];
  return x`
    <div
      class="card${fadingOut ? " fading-out" : ""}"
      style="padding:16px 18px;display:flex;flex-direction:column;"
    >
      <div class="card-header" style="margin-bottom:0;">
        ${
          bulkMode
            ? x`
              <label class="card-select">
                <input
                  type="checkbox"
                  .checked=${!!selectedKeys[cardKey]}
                  @change=${(e5) => {
                    host._selectedSuggestionKeys = {
                      ...host._selectedSuggestionKeys,
                      [cardKey]: e5.target.checked,
                    };
                  }}
                />
              </label>
            `
            : ""
        }
        <h3 style="flex:1;font-size:14px;margin:0;">${item.title}</h3>
      </div>

      ${
        item.subtitle
          ? x`
            <div
              style="font-size:12px;color:var(--secondary-text-color);line-height:1.5;margin-top:8px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;"
            >
              ${item.subtitle}
            </div>
          `
          : ""
      }
      ${
        item.risk?.level === "elevated"
          ? x`
            <div
              class="proposal-status"
              style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25); margin-top:8px;font-size:12px;"
            >
              <ha-icon icon="mdi:alert-outline"></ha-icon>
              <span>${item.risk.summary}</span>
            </div>
          `
          : ""
      }

      <div class="card-tabs" style="margin-top:12px;">
        ${
          hasFlow
            ? x`
              <button
                class="card-tab ${activeTab === "flow" ? "active" : ""}"
                @click=${() => {
                  host._cardActiveTab = {
                    ...host._cardActiveTab,
                    [cardKey]: activeTab === "flow" ? null : "flow",
                  };
                }}
              >
                <ha-icon
                  icon="mdi:sitemap-outline"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                Flow
              </button>
              <span class="card-tab-sep">|</span>
            `
            : ""
        }
        <button
          class="card-tab ${activeTab === "yaml" ? "active" : ""}"
          @click=${() => {
            host._cardActiveTab = {
              ...host._cardActiveTab,
              [cardKey]: activeTab === "yaml" ? null : "yaml",
            };
          }}
        >
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          YAML
        </button>
        <ha-icon
          icon="mdi:chevron-down"
          class="card-chevron ${activeTab ? "open" : ""}"
          style="margin-left:auto;"
          @click=${() => {
            host._cardActiveTab = {
              ...host._cardActiveTab,
              [cardKey]: activeTab ? null : hasFlow ? "flow" : "yaml",
            };
          }}
        ></ha-icon>
      </div>

      ${activeTab === "flow" && hasFlow ? renderAutomationFlowchart(host, automationData) : ""}
      ${
        activeTab === "yaml"
          ? x`
            <div style="margin-top:6px;">
              <ha-code-editor
                mode="yaml"
                .value=${displayYaml}
                @value-changed=${(e5) => {
                  host._editedYaml = {
                    ...host._editedYaml,
                    [cardKey]: e5.detail.value,
                  };
                }}
                autocomplete-entities
                style="--code-mirror-font-size:12px;"
              ></ha-code-editor>
            </div>
          `
          : ""
      }

      <div
        style="display:flex;align-items:center;gap:6px;margin-top:auto;padding-top:12px;"
      >
        <button
          class="btn btn-primary"
          style="flex:1;justify-content:center;"
          ?disabled=${accepting}
          @click=${() =>
            isProactive
              ? host._acceptProactiveSuggestion(item._suggestionId, editedYaml)
              : host._createSuggestionWithEdits(
                  item._auto,
                  cardKey,
                  item.automationYaml,
                )}
        >
          <ha-icon icon="mdi:check" style="--mdc-icon-size:13px;"></ha-icon>
          ${accepting ? "Creating\u2026" : "Accept"}
        </button>
        <button
          class="btn btn-outline"
          style="flex:1;justify-content:center;"
          ?disabled=${dismissing}
          @click=${() => (isProactive ? host._dismissProactiveSuggestion(item._suggestionId) : host._discardSuggestion(item._original))}
        >
          <ha-icon icon="mdi:close" style="--mdc-icon-size:13px;"></ha-icon>
          ${dismissing ? "Dismissing\u2026" : "Dismiss"}
        </button>
      </div>
    </div>
  `;
}
function renderSuggestionsSection(host) {
  const qualified = buildQualified(host);
  const filtered = applyFilters(host, qualified);
  const totalCount = qualified.length;
  const isDev = !!host._config?.developer_mode;
  const visibleCount = host._suggestionsVisibleCount || COLLAPSED_COUNT;
  const visibleItems = filtered.slice(0, visibleCount);
  const remainingCount = filtered.length - visibleCount;
  const expanded = visibleCount > COLLAPSED_COUNT;
  const bulkMode = !!host._suggestionBulkMode;
  const selectedKeys = host._selectedSuggestionKeys || {};
  const selectedCount = Object.values(selectedKeys).filter(Boolean).length;
  return x`
    <div class="section-card suggestions-section">
      <div class="section-card-header">
        <h3>Suggested for you</h3>
        ${totalCount > 0 ? x`<span class="badge">${totalCount} new</span>` : ""}
        ${
          isDev
            ? x`
              <div
                style="margin-left:auto;display:flex;align-items:center;gap:8px;"
              >
                <button
                  class="btn"
                  ?disabled=${host._loadingProactive}
                  @click=${() => host._triggerPatternScan()}
                >
                  <ha-icon
                    icon="mdi:refresh"
                    style="--mdc-icon-size:13px;"
                  ></ha-icon>
                  ${host._loadingProactive ? "Scanning\u2026" : "Scan Now"}
                </button>
                <button
                  class="btn btn-primary"
                  style="white-space:nowrap;"
                  ?disabled=${host._generatingSuggestions}
                  @click=${() => host._triggerGenerateSuggestions()}
                >
                  ${
                    host._generatingSuggestions
                      ? x`<span
                        class="spinner"
                        style="width:14px;height:14px;border-width:2px;vertical-align:middle;"
                      ></span>`
                      : x`<ha-icon
                        icon="mdi:auto-fix"
                        style="--mdc-icon-size:13px;"
                      ></ha-icon>`
                  }
                  ${host._generatingSuggestions ? "Analyzing\u2026" : "Generate"}
                </button>
              </div>
            `
            : ""
        }
      </div>

      <div class="section-card-subtitle">
        Based on observed patterns and AI analysis in your home.
      </div>

      ${
        totalCount === 0
          ? x`
            <p style="opacity:0.45;margin:0;font-size:13px;">
              No suggestions yet. Tap "Generate" to analyze your home.
            </p>
          `
          : x`
            ${
              expanded
                ? x`<div class="filter-row" style="margin-bottom:12px;">
                  <div class="filter-input-wrap" style="flex:0 1 260px;">
                    <ha-icon icon="mdi:magnify"></ha-icon>
                    <input
                      type="text"
                      placeholder="Filter suggestions…"
                      .value=${host._suggestionFilter}
                      @input=${(e5) => {
                        host._suggestionFilter = e5.target.value;
                        host._suggestionsVisibleCount = COLLAPSED_COUNT;
                      }}
                    />
                    ${
                      host._suggestionFilter
                        ? x`<ha-icon
                          icon="mdi:close-circle"
                          style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                          @click=${() => {
                            host._suggestionFilter = "";
                            host._suggestionsVisibleCount = COLLAPSED_COUNT;
                          }}
                        ></ha-icon>`
                        : ""
                    }
                  </div>
                  ${
                    isDev
                      ? x`
                        <div class="status-pills">
                          ${[
                            ["all", "All"],
                            ["pattern", "Patterns"],
                            ["ai", "AI"],
                          ].map(
                            ([val, label]) => x`
                              <button
                                class="status-pill ${(host._suggestionSourceFilter || "all") === val ? "active" : ""}"
                                @click=${() => {
                                  host._suggestionSourceFilter = val;
                                  host._suggestionsVisibleCount =
                                    COLLAPSED_COUNT;
                                }}
                              >
                                ${label}
                              </button>
                            `,
                          )}
                        </div>
                      `
                      : ""
                  }
                  <select
                    class="sort-select"
                    .value=${host._suggestionSortBy || "recent"}
                    @change=${(e5) => {
                      host._suggestionSortBy = e5.target.value;
                    }}
                  >
                    <option value="recent">Recent</option>
                    <option value="alpha">Alphabetical</option>
                  </select>
                  <div
                    style="margin-left:auto;display:flex;align-items:center;gap:8px;"
                  >
                    ${
                      bulkMode
                        ? x`
                          <span style="font-size:12px;opacity:0.7;">
                            ${selectedCount} selected
                          </span>
                          <button
                            class="btn btn-primary"
                            ?disabled=${selectedCount === 0}
                            @click=${() => {
                              for (const item of filtered) {
                                if (selectedKeys[item.cardKey]) {
                                  if (item.type === "proactive") {
                                    host._acceptProactiveSuggestion(
                                      item._suggestionId,
                                    );
                                  } else {
                                    host._createSuggestionWithEdits(
                                      item._auto,
                                      item.cardKey,
                                      item.automationYaml,
                                    );
                                  }
                                }
                              }
                              host._selectedSuggestionKeys = {};
                              host._suggestionBulkMode = false;
                            }}
                          >
                            Accept selected
                          </button>
                          <button
                            class="btn btn-outline"
                            ?disabled=${selectedCount === 0}
                            @click=${() => {
                              for (const item of filtered) {
                                if (selectedKeys[item.cardKey]) {
                                  if (item.type === "proactive") {
                                    host._dismissProactiveSuggestion(
                                      item._suggestionId,
                                    );
                                  } else {
                                    host._discardSuggestion(item._original);
                                  }
                                }
                              }
                              host._selectedSuggestionKeys = {};
                              host._suggestionBulkMode = false;
                            }}
                          >
                            Dismiss selected
                          </button>
                          <button
                            class="btn btn-outline"
                            @click=${() => {
                              host._suggestionBulkMode = false;
                              host._selectedSuggestionKeys = {};
                            }}
                          >
                            Done
                          </button>
                        `
                        : x`
                          <button
                            class="btn btn-outline"
                            @click=${() => {
                              host._suggestionBulkMode = true;
                            }}
                          >
                            <ha-icon
                              icon="mdi:checkbox-multiple-outline"
                              style="--mdc-icon-size:14px;"
                            ></ha-icon>
                            Bulk edit
                          </button>
                        `
                    }
                  </div>
                </div>`
                : ""
            }

            <div class="automations-grid">
              ${masonryColumns(
                visibleItems.map((item) =>
                  renderSuggestionCard(host, item, bulkMode, selectedKeys),
                ),
              )}
            </div>

            ${
              remainingCount > 0
                ? x`
                  <button
                    class="show-more-link"
                    @click=${() => {
                      host._suggestionsVisibleCount = visibleCount + 10;
                    }}
                  >
                    Show more suggestions
                  </button>
                `
                : ""
            }
          `
      }
    </div>
  `;
}

// src/panel/render-automations.js
function renderAutomationFlowchart(host, auto) {
  if (!auto) return x``;
  const triggers = (() => {
    const t3 = auto.triggers ?? auto.trigger ?? [];
    return Array.isArray(t3) ? t3 : [t3];
  })();
  const conditions = (() => {
    const c3 = auto.conditions ?? auto.condition ?? [];
    return Array.isArray(c3) ? c3 : [c3];
  })().filter(Boolean);
  const actions = (() => {
    const a4 = auto.actions ?? auto.action ?? [];
    return Array.isArray(a4) ? a4 : [a4];
  })();
  if (!triggers.length && !actions.length) return x``;
  return x`
    <div class="flow-chart">
      <div class="flow-section">
        <div class="flow-label">Trigger</div>
        ${triggers.map(
          (t3) => x`<div class="flow-node trigger-node">
              ${describeFlowItem(host.hass, t3)}
            </div>`,
        )}
      </div>
      ${
        conditions.length
          ? x`
            <div class="flow-arrow">↓</div>
            <div class="flow-section">
              <div class="flow-label">Condition</div>
              ${conditions.map(
                (c3) => x`<div class="flow-node condition-node">
                    ${describeFlowItem(host.hass, c3)}
                  </div>`,
              )}
            </div>
          `
          : ""
      }
      <div class="flow-arrow">↓</div>
      <div class="flow-section">
        <div class="flow-label">Actions</div>
        ${actions.map(
          (a4, i5) => x`
            ${i5 > 0 ? x`<div class="flow-arrow-sm">↓</div>` : ""}
            <div class="flow-node action-node">
              ${describeFlowItem(host.hass, a4)}
            </div>
          `,
        )}
      </div>
    </div>
  `;
}
function renderProposalCard(host, msg, msgIndex) {
  const status = msg.automation_status;
  const automation = msg.automation;
  const yaml = msg.automation_yaml || "";
  const risk = msg.risk_assessment || automation?.risk_assessment || null;
  const scrutinyTags = risk?.scrutiny_tags || [];
  if (status === "saved") {
    return x`
      <div class="proposal-card" style="margin-top:12px;">
        <div class="proposal-header">
          <ha-icon icon="mdi:check-circle"></ha-icon>
          Automation Created
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div class="proposal-status saved">
            <ha-icon icon="mdi:check"></ha-icon> Saved and enabled
          </div>
        </div>
      </div>
    `;
  }
  if (status === "declined") {
    return x`
      <div class="proposal-card" style="margin-top:12px; opacity:0.6;">
        <div class="proposal-header" style="color:var(--secondary-text-color);">
          <ha-icon icon="mdi:close-circle-outline"></ha-icon>
          Automation Declined
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div class="proposal-status declined">
            Dismissed. You can refine it by replying below.
          </div>
        </div>
      </div>
    `;
  }
  if (status === "refining") {
    return x`
      <div class="proposal-card" style="margin-top:12px; opacity:0.75;">
        <div class="proposal-header" style="color:var(--selora-accent);">
          <ha-icon icon="mdi:pencil-circle-outline"></ha-icon>
          Being Refined
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div
            class="proposal-status"
            style="background:var(--selora-zinc-800); color:var(--selora-accent); border:1px solid var(--selora-zinc-700); border-radius:8px; padding:8px 12px;"
          >
            <ha-icon icon="mdi:arrow-down"></ha-icon>
            Refinement requested — see the updated proposal below.
          </div>
        </div>
      </div>
    `;
  }
  const yamlOpen = host._yamlOpen && host._yamlOpen[msgIndex];
  const yamlKey = `proposal_${msgIndex}`;
  const hasEdits =
    host._editedYaml[yamlKey] !== void 0 && host._editedYaml[yamlKey] !== yaml;
  return x`
    <div class="proposal-card">
      <div class="proposal-header">
        <ha-icon icon="mdi:lightning-bolt"></ha-icon>
        Automation Proposal
      </div>
      <div class="proposal-body">
        <div class="proposal-name">${automation.alias}</div>

        ${
          msg.description
            ? x`
              <div class="proposal-description-label">
                What this automation does
              </div>
              <div class="proposal-description">${msg.description}</div>
            `
            : ""
        }
        ${
          risk?.level === "elevated"
            ? x`
              <div
                class="proposal-status"
                style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25);"
              >
                <ha-icon icon="mdi:alert-outline"></ha-icon>
                <div>
                  <strong>Elevated risk review recommended.</strong>
                  <div style="margin-top:4px;">${risk.summary}</div>
                  ${
                    risk.reasons?.length
                      ? x`<div style="margin-top:6px; font-size:12px;">
                        ${risk.reasons.join(" ")}
                      </div>`
                      : ""
                  }
                </div>
              </div>
            `
            : ""
        }
        ${renderAutomationFlowchart(host, automation)}

        <div class="yaml-toggle" @click=${() => toggleYaml(host, msgIndex)}>
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen ? "Hide YAML" : "Edit YAML"}
        </div>
        ${yamlOpen ? host._renderYamlEditor(yamlKey, yaml) : ""}

        <div class="proposal-verify">
          ${hasEdits ? "Your YAML edits will be used when you accept." : "Does the flow above match what you intended?"}
        </div>

        <div class="proposal-actions">
          <button
            class="btn btn-success"
            @click=${() => host._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Accept &amp; Save
          </button>
          <button
            class="btn btn-outline"
            @click=${() => host._refineAutomation(msgIndex, automation, msg.description)}
          >
            <ha-icon icon="mdi:pencil" style="--mdc-icon-size:14px;"></ha-icon>
            Refine
          </button>
          <button
            class="btn btn-danger"
            @click=${() => host._declineAutomation(msgIndex)}
          >
            <ha-icon icon="mdi:close" style="--mdc-icon-size:14px;"></ha-icon>
            Decline
          </button>
        </div>
      </div>
    </div>
  `;
}
function toggleYaml(host, msgIndex) {
  host._yamlOpen = {
    ...(host._yamlOpen || {}),
    [msgIndex]: !(host._yamlOpen || {})[msgIndex],
  };
  host.requestUpdate();
}
function masonryColumns(cards, cols = 3, firstColFooter = null) {
  const w2 = window.innerWidth;
  const numCols = w2 <= 600 ? 1 : w2 <= 1e3 ? 2 : cols;
  const buckets = Array.from({ length: numCols }, () => []);
  cards.forEach((c3, i5) => buckets[i5 % numCols].push(c3));
  return buckets.map(
    (col, i5) => x`<div class="masonry-col">
        ${col}${i5 === 0 && firstColFooter ? firstColFooter : ""}
      </div>`,
  );
}
function renderAutomations(host) {
  const filterText = (host._automationFilter || "").toLowerCase();
  const statusFilter = host._statusFilter || "all";
  const sortBy = host._sortBy || "recent";
  let filteredAutomations = [...host._automations];
  if (statusFilter === "all") {
    filteredAutomations = filteredAutomations.filter((a4) => !a4.is_deleted);
  } else if (statusFilter === "enabled") {
    filteredAutomations = filteredAutomations.filter(
      (a4) => !a4.is_deleted && host._automationIsEnabled(a4),
    );
  } else if (statusFilter === "disabled") {
    filteredAutomations = filteredAutomations.filter(
      (a4) => !a4.is_deleted && !host._automationIsEnabled(a4),
    );
  } else if (statusFilter === "deleted") {
    filteredAutomations = filteredAutomations.filter((a4) => a4.is_deleted);
  }
  if (filterText) {
    filteredAutomations = filteredAutomations.filter((a4) =>
      (a4.alias || "").toLowerCase().includes(filterText),
    );
  }
  if (sortBy === "recent") {
    filteredAutomations.sort((a4, b2) => {
      const aTime = a4.last_triggered
        ? new Date(a4.last_triggered).getTime()
        : 0;
      const bTime = b2.last_triggered
        ? new Date(b2.last_triggered).getTime()
        : 0;
      return bTime - aTime;
    });
  } else if (sortBy === "alpha") {
    filteredAutomations.sort((a4, b2) =>
      (a4.alias || "").localeCompare(b2.alias || ""),
    );
  } else if (sortBy === "enabled_first") {
    filteredAutomations.sort((a4, b2) => {
      const aOn = host._automationIsEnabled(a4) ? 0 : 1;
      const bOn = host._automationIsEnabled(b2) ? 0 : 1;
      return aOn - bOn;
    });
  }
  const enabledCount = host._automations.filter((a4) =>
    host._automationIsEnabled(a4),
  ).length;
  const disabledCount = host._automations.filter(
    (a4) => !host._automationIsEnabled(a4) && !a4.is_deleted,
  ).length;
  const deletedCount = host._automations.filter((a4) => a4.is_deleted).length;
  const perPage = host._autosPerPage || 10;
  const totalAutoPages = Math.max(
    1,
    Math.ceil(filteredAutomations.length / perPage),
  );
  const safeAutoPage = Math.min(host._automationsPage, totalAutoPages);
  const pagedAutomations = filteredAutomations.slice(
    (safeAutoPage - 1) * perPage,
    safeAutoPage * perPage,
  );
  const selectableAutomations = filteredAutomations.filter(
    (a4) => !a4._draft && a4.automation_id,
  );
  const selectableIds = selectableAutomations.map((a4) => a4.automation_id);
  const selectedIds = host._getSelectedAutomationIds();
  const selectedVisibleCount = selectableIds.filter(
    (id) => host._selectedAutomationIds[id],
  ).length;
  const allVisibleSelected =
    selectableIds.length > 0 && selectedVisibleCount === selectableIds.length;
  const partiallyVisibleSelected =
    selectedVisibleCount > 0 && !allVisibleSelected;
  const hiddenSelectedCount = Math.max(
    0,
    selectedIds.length - selectedVisibleCount,
  );
  const bulkDisabled = selectedIds.length === 0 || host._bulkActionInProgress;
  return x`
    <div class="scroll-view" @click=${() => host._closeBurgerMenus()}>
      ${renderSuggestionsSection(host)}
      <div class="section-card">
        <div class="section-card-header">
          <h3>Your Automations</h3>
        </div>
        ${
          host._automations.length > 0
            ? x`
              <div class="filter-row" style="margin-top:12px;">
                <div class="filter-input-wrap" style="flex:0 1 260px;">
                  <ha-icon icon="mdi:magnify"></ha-icon>
                  <input
                    type="text"
                    placeholder="Filter automations…"
                    .value=${host._automationFilter}
                    @input=${(e5) => {
                      host._automationFilter = e5.target.value;
                      host._automationsPage = 1;
                    }}
                  />
                  ${
                    host._automationFilter
                      ? x`<ha-icon
                        icon="mdi:close-circle"
                        style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                        @click=${() => {
                          host._automationFilter = "";
                          host._automationsPage = 1;
                        }}
                      ></ha-icon>`
                      : ""
                  }
                </div>
                <div class="status-pills">
                  ${["all", "enabled", "disabled", "deleted"].map(
                    (s6) => x`
                      <button
                        class="status-pill ${host._statusFilter === s6 ? "active" : ""}"
                        @click=${() => {
                          host._statusFilter = s6;
                          host._automationsPage = 1;
                        }}
                      >
                        ${s6.charAt(0).toUpperCase() + s6.slice(1)}
                      </button>
                    `,
                  )}
                </div>
                <select
                  class="sort-select"
                  .value=${host._sortBy}
                  @change=${(e5) => {
                    host._sortBy = e5.target.value;
                  }}
                >
                  <option value="recent">Recent activity</option>
                  <option value="alpha">Alphabetical</option>
                  <option value="enabled_first">Enabled first</option>
                </select>
                <div
                  style="margin-left:auto;display:flex;align-items:center;gap:8px;"
                >
                  <button
                    class="btn btn-primary"
                    style="white-space:nowrap;"
                    @click=${() => {
                      host._newAutoName = "";
                      host._showNewAutoDialog = true;
                    }}
                  >
                    <ha-icon
                      icon="mdi:plus"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    New Automation
                  </button>
                </div>
              </div>
              <div
                class="automations-summary"
                style="display:flex;align-items:center;justify-content:space-between;"
              >
                <span>
                  ${filteredAutomations.length} existing
                  automation${filteredAutomations.length !== 1 ? "s" : ""}
                  (${enabledCount} enabled, ${disabledCount}
                  disabled${deletedCount > 0 ? `, ${deletedCount} deleted` : ""})
                </span>
                ${
                  host._bulkEditMode
                    ? x`
                      <div style="display:flex;align-items:center;gap:10px;">
                        <label class="bulk-select-all">
                          <input
                            type="checkbox"
                            ?checked=${allVisibleSelected}
                            .indeterminate=${partiallyVisibleSelected}
                            ?disabled=${selectableIds.length === 0 || host._bulkActionInProgress}
                            @change=${(e5) =>
                              host._toggleSelectAllFiltered(
                                filteredAutomations,
                                e5.target.checked,
                              )}
                          />
                          <span>Select all</span>
                        </label>
                        <button
                          class="btn btn-outline"
                          @click=${() => {
                            host._bulkEditMode = false;
                            host._clearAutomationSelection();
                          }}
                        >
                          Done
                        </button>
                      </div>
                    `
                    : x`
                      <button
                        class="btn btn-outline"
                        @click=${() => {
                          host._bulkEditMode = true;
                        }}
                      >
                        <ha-icon
                          icon="mdi:checkbox-multiple-outline"
                          style="--mdc-icon-size:14px;"
                        ></ha-icon>
                        Bulk edit
                      </button>
                    `
                }
              </div>
              ${
                host._bulkEditMode && selectedIds.length > 0
                  ? x`
                    <div class="bulk-actions-row">
                      <div class="left">
                        ${selectedIds.length}
                        selected${
                          hiddenSelectedCount > 0
                            ? x` <span style="opacity:0.65;font-weight:500;"
                              >(${hiddenSelectedCount} hidden by filter)</span
                            >`
                            : ""
                        }
                        ${
                          host._bulkActionInProgress
                            ? x`<span style="opacity:0.75;font-weight:500;">
                              · ${host._bulkActionLabel}</span
                            >`
                            : ""
                        }
                      </div>
                      <div class="actions">
                        <button
                          class="btn btn-outline"
                          ?disabled=${bulkDisabled}
                          @click=${() => host._bulkToggleSelected(true)}
                        >
                          ${host._bulkActionInProgress ? "Working\u2026" : "Enable all"}
                        </button>
                        <button
                          class="btn btn-outline"
                          ?disabled=${bulkDisabled}
                          @click=${() => host._bulkToggleSelected(false)}
                        >
                          ${host._bulkActionInProgress ? "Working\u2026" : "Disable all"}
                        </button>
                        <button
                          class="btn btn-outline btn-danger"
                          ?disabled=${bulkDisabled}
                          @click=${() => host._bulkSoftDeleteSelected()}
                        >
                          ${host._bulkActionInProgress ? "Working\u2026" : "Soft-delete selected"}
                        </button>
                        <button
                          class="btn btn-ghost"
                          ?disabled=${host._bulkActionInProgress}
                          @click=${() => host._clearAutomationSelection()}
                        >
                          Clear
                        </button>
                      </div>
                    </div>
                  `
                  : ""
              }
              <div class="automations-list">
                ${pagedAutomations.map((a4) => {
                  const isDraft = !!a4._draft;
                  const isOn = host._automationIsEnabled(a4);
                  const automationId = a4.automation_id || "";
                  const hasAutomationId = !!automationId;
                  const canToggle =
                    hasAutomationId && !host._bulkActionInProgress;
                  const deleting = host._deletingAutomation[automationId];
                  const loadingChat = host._loadingToChat[automationId];
                  const burgerOpen = host._openBurgerMenu === automationId;
                  const cardExpanded = !!host._cardActiveTab[a4.entity_id];
                  const ago = formatTimeAgo(a4.last_triggered);
                  const lastRun = ago ? ago : !isOn ? "Disabled" : "Never";
                  return x`
                    <div
                      class="auto-row${cardExpanded ? " expanded" : ""}${!isDraft && !isOn ? " disabled" : ""}${host._highlightedAutomation === a4.entity_id ? " highlighted" : ""}"
                      data-entity-id="${a4.entity_id}"
                    >
                      <div
                        class="auto-row-main"
                        @click=${(e5) => {
                          if (
                            e5.target.closest(
                              ".toggle-switch, .burger-menu-wrapper, .burger-dropdown, .burger-item, .card-select, .rename-input, .rename-save-btn, .btn",
                            )
                          )
                            return;
                          const current = host._cardActiveTab[a4.entity_id];
                          if (current) {
                            host._cardActiveTab = {
                              ...host._cardActiveTab,
                              [a4.entity_id]: null,
                            };
                          } else {
                            const defaultTab =
                              a4.trigger?.length || a4.action?.length
                                ? "flow"
                                : a4.yaml_text
                                  ? "yaml"
                                  : hasAutomationId
                                    ? "history"
                                    : null;
                            host._cardActiveTab = {
                              ...host._cardActiveTab,
                              [a4.entity_id]: defaultTab,
                            };
                          }
                        }}
                      >
                        ${
                          host._bulkEditMode && hasAutomationId
                            ? x`
                              <label class="card-select">
                                <input
                                  type="checkbox"
                                  .checked=${!!host._selectedAutomationIds[automationId]}
                                  ?disabled=${host._bulkActionInProgress}
                                  @click=${(e5) => e5.stopPropagation()}
                                  @change=${(e5) =>
                                    host._toggleAutomationSelection(
                                      automationId,
                                      e5,
                                    )}
                                />
                              </label>
                            `
                            : ""
                        }
                        <div
                          class="auto-row-name"
                          data-last-run="Last run: ${lastRun}"
                        >
                          ${
                            host._editingAlias === automationId
                              ? x`
                                <input
                                  class="rename-input"
                                  data-id="${automationId}"
                                  .value=${host._editingAliasValue}
                                  @input=${(e5) => {
                                    host._editingAliasValue = e5.target.value;
                                  }}
                                  @click=${(e5) => e5.stopPropagation()}
                                  @keydown=${(e5) => {
                                    if (e5.key === "Enter")
                                      host._saveRenameAutomation(automationId);
                                    if (e5.key === "Escape")
                                      host._cancelRenameAutomation();
                                  }}
                                />
                                <button
                                  class="rename-save-btn"
                                  title="Save"
                                  @click=${() => host._saveRenameAutomation(automationId)}
                                >
                                  <ha-icon
                                    icon="mdi:check"
                                    style="--mdc-icon-size:16px;"
                                  ></ha-icon>
                                </button>
                              `
                              : x`<span class="auto-row-title"
                                >${a4.alias}</span
                              >`
                          }
                          ${
                            a4.description
                              ? x`<span class="auto-row-desc"
                                >${a4.description.replace(
                                  /^\[Selora AI\]\s*/,
                                  "",
                                )}</span
                              >`
                              : ""
                          }
                          <span class="auto-row-mobile-meta">
                            <span>Last run: ${lastRun}</span>
                            <ha-icon
                              icon="mdi:chevron-down"
                              class="card-chevron ${cardExpanded ? "open" : ""}"
                              style="--mdc-icon-size:16px;"
                            ></ha-icon>
                          </span>
                        </div>
                        <span class="auto-row-last-run"
                          ><span class="last-run-prefix">Last run: </span
                          >${lastRun}${
                            a4.last_triggered
                              ? x`<span class="setting-tooltip"
                                >Last run:
                                ${new Date(
                                  a4.last_triggered,
                                ).toLocaleString()}</span
                              >`
                              : ""
                          }
                        </span>
                        <label
                          class="toggle-switch"
                          title="${canToggle ? (isOn ? "Enabled" : "Disabled") : "Unavailable"}"
                          style="flex-shrink:0;${canToggle ? "" : "opacity:0.45;cursor:not-allowed;"}"
                          @click=${(e5) => {
                            e5.stopPropagation();
                            if (!canToggle) {
                              host._showToast(
                                "Unable to toggle: automation id was not resolved. Reload and try again.",
                                "error",
                              );
                            }
                          }}
                        >
                          <input
                            type="checkbox"
                            .checked=${isOn}
                            ?disabled=${!canToggle}
                            @click=${(e5) => e5.stopPropagation()}
                            @change=${(e5) => {
                              if (!canToggle) return;
                              host._toggleAutomation(
                                a4.entity_id,
                                automationId,
                                e5.target.checked,
                              );
                            }}
                          />
                          <div class="toggle-track ${isOn ? "on" : ""}">
                            <div class="toggle-thumb"></div>
                          </div>
                        </label>
                        ${
                          hasAutomationId
                            ? x`
                              <div class="burger-menu-wrapper">
                                <button
                                  class="burger-btn"
                                  @click=${(e5) => host._toggleBurgerMenu(automationId, e5)}
                                  ?disabled=${host._bulkActionInProgress}
                                  title="More actions"
                                >
                                  <ha-icon
                                    icon="mdi:dots-vertical"
                                    style="--mdc-icon-size:16px;"
                                  ></ha-icon>
                                </button>
                                ${
                                  burgerOpen
                                    ? x`
                                      <div class="burger-dropdown">
                                        <button
                                          class="burger-item"
                                          @click=${(e5) => {
                                            e5.stopPropagation();
                                            host._openBurgerMenu = null;
                                            host._loadAutomationToChat(
                                              automationId,
                                            );
                                          }}
                                          ?disabled=${loadingChat}
                                        >
                                          <ha-icon
                                            icon="mdi:chat-processing-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${loadingChat ? "Loading\u2026" : "Refine in chat"}
                                        </button>
                                        <button
                                          class="burger-item"
                                          @click=${(e5) => {
                                            e5.stopPropagation();
                                            host._startRenameAutomation(
                                              automationId,
                                              a4.alias,
                                            );
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:pencil-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          Rename
                                        </button>
                                        <button
                                          class="burger-item"
                                          @click=${(e5) => {
                                            e5.stopPropagation();
                                            host._openBurgerMenu = null;
                                            window.history.pushState(
                                              null,
                                              "",
                                              `/config/automation/edit/${automationId}`,
                                            );
                                            window.dispatchEvent(
                                              new Event("location-changed"),
                                            );
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:open-in-new"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          Edit in HA
                                        </button>
                                        <button
                                          class="burger-item danger"
                                          ?disabled=${deleting}
                                          @click=${(e5) => {
                                            e5.stopPropagation();
                                            host._openBurgerMenu = null;
                                            host._softDeleteAutomation(
                                              automationId,
                                            );
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:trash-can-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${deleting ? "Deleting\u2026" : "Delete"}
                                        </button>
                                      </div>
                                    `
                                    : ""
                                }
                              </div>
                            `
                            : ""
                        }
                      </div>
                      ${
                        cardExpanded
                          ? x`
                            <div class="auto-row-expand">
                              <div class="card-tabs" style="margin-top:0;">
                                ${
                                  a4.trigger?.length || a4.action?.length
                                    ? x`
                                      <button
                                        class="card-tab ${host._cardActiveTab[a4.entity_id] === "flow" ? "active" : ""}"
                                        @click=${() => {
                                          host._cardActiveTab = {
                                            ...host._cardActiveTab,
                                            [a4.entity_id]:
                                              host._cardActiveTab[
                                                a4.entity_id
                                              ] === "flow"
                                                ? null
                                                : "flow",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:sitemap-outline"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        Flow
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                    : ""
                                }
                                ${
                                  a4.yaml_text
                                    ? x`
                                      <button
                                        class="card-tab ${host._cardActiveTab[a4.entity_id] === "yaml" ? "active" : ""}"
                                        @click=${() => {
                                          host._cardActiveTab = {
                                            ...host._cardActiveTab,
                                            [a4.entity_id]:
                                              host._cardActiveTab[
                                                a4.entity_id
                                              ] === "yaml"
                                                ? null
                                                : "yaml",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:code-braces"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        YAML
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                    : ""
                                }
                                ${
                                  hasAutomationId
                                    ? x`
                                      <button
                                        class="card-tab ${host._cardActiveTab[a4.entity_id] === "history" ? "active" : ""}"
                                        @click=${() => {
                                          const isActive =
                                            host._cardActiveTab[
                                              a4.entity_id
                                            ] === "history";
                                          host._cardActiveTab = {
                                            ...host._cardActiveTab,
                                            [a4.entity_id]: isActive
                                              ? null
                                              : "history",
                                          };
                                          if (
                                            !isActive &&
                                            !host._versions[automationId]
                                          ) {
                                            host._versionHistoryOpen = {
                                              ...host._versionHistoryOpen,
                                              [automationId]: true,
                                            };
                                            host._loadVersionHistory(
                                              automationId,
                                            );
                                          }
                                        }}
                                      >
                                        History
                                      </button>
                                    `
                                    : ""
                                }
                              </div>
                              ${host._cardActiveTab[a4.entity_id] === "flow" && (a4.trigger?.length || a4.action?.length) ? renderAutomationFlowchart(host, a4) : ""}
                              ${
                                host._cardActiveTab[a4.entity_id] === "yaml" &&
                                a4.yaml_text
                                  ? host._renderYamlEditor(
                                      `yaml_${a4.entity_id}`,
                                      a4.yaml_text,
                                      (key) =>
                                        host._saveActiveAutomationYaml(
                                          a4.automation_id,
                                          key,
                                        ),
                                    )
                                  : ""
                              }
                              ${host._cardActiveTab[a4.entity_id] === "history" && hasAutomationId ? host._renderVersionHistoryDrawer(a4) : ""}
                            </div>
                          `
                          : ""
                      }
                    </div>
                  `;
                })}
              </div>
              ${
                totalAutoPages > 1
                  ? x`
                    <div class="pagination">
                      <button
                        class="btn btn-outline"
                        ?disabled=${safeAutoPage <= 1}
                        @click=${() => {
                          host._automationsPage = safeAutoPage - 1;
                        }}
                      >
                        ‹ Prev
                      </button>
                      <span class="page-info"
                        >Page ${safeAutoPage} of ${totalAutoPages} ·
                        ${filteredAutomations.length} automations</span
                      >
                      <label class="per-page-label"
                        >Per page:
                        <select
                          class="per-page-select"
                          .value=${String(host._autosPerPage)}
                          @change=${(e5) => {
                            host._autosPerPage = Number(e5.target.value);
                            host._automationsPage = 1;
                          }}
                        >
                          <option value="10">10</option>
                          <option value="20">20</option>
                          <option value="50">50</option>
                        </select>
                      </label>
                      <button
                        class="btn btn-outline"
                        ?disabled=${safeAutoPage >= totalAutoPages}
                        @click=${() => {
                          host._automationsPage = safeAutoPage + 1;
                        }}
                      >
                        Next ›
                      </button>
                    </div>
                  `
                  : ""
              }
              ${
                filteredAutomations.length === 0 && host._automations.length > 0
                  ? x`<div
                    style="text-align:center;opacity:0.45;padding:24px 0;"
                  >
                    No automations match "${host._automationFilter}"
                  </div>`
                  : ""
              }
            `
            : x`<div style="text-align:center;padding:32px 0;">
              <ha-icon
                icon="mdi:robot-vacuum-variant"
                style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"
              ></ha-icon>
              <p style="opacity:0.45;margin:0 0 12px;">No automations yet.</p>
              <button
                class="btn btn-primary"
                @click=${() => {
                  host._newAutoName = "";
                  host._showNewAutoDialog = true;
                }}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                New Automation
              </button>
            </div>`
        }
      </div>
      ${host._renderDiffViewer()} ${host._renderNewAutomationDialog()}
    </div>
  `;
}

// src/panel/render-settings.js
function renderSettings(host) {
  if (!host._config) {
    return x`
      <div
        class="scroll-view"
        style="display:flex; justify-content:center; padding-top:64px;"
      >
        <ha-circular-progress active></ha-circular-progress>
      </div>
    `;
  }
  const isAnthropic = host._config.llm_provider === "anthropic";
  const isOpenAI = host._config.llm_provider === "openai";
  return x`
    <div class="scroll-view">
      <div class="settings-form">
        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>LLM Provider</h3>
          </div>
          <div class="form-group">
            <label>Provider</label>
            <select
              class="form-select"
              .value=${host._config.llm_provider}
              @change=${(e5) => host._updateConfig("llm_provider", e5.target.value)}
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama (Local)</option>
              <option disabled>Selora AI Local (Coming soon)</option>
              <option disabled>Selora AI Cloud (Coming soon)</option>
            </select>
          </div>

          ${
            isAnthropic
              ? x`
                <div class="form-group">
                  <label>API Key</label>
                  ${
                    host._config.anthropic_api_key_set
                      ? x`<div class="key-hint">
                        ${host._config.anthropic_api_key_hint}
                      </div>`
                      : x`<div class="key-not-set">No API key set</div>`
                  }
                  <ha-textfield
                    label="${host._config.anthropic_api_key_set ? "Enter new key to replace" : "Enter API key"}"
                    type="password"
                    .value=${host._newApiKey}
                    @input=${(e5) => (host._newApiKey = e5.target.value)}
                    placeholder="sk-ant-..."
                    style="margin-top:8px;width:100%;"
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Model"
                    .value=${host._config.anthropic_model}
                    @input=${(e5) => host._updateConfig("anthropic_model", e5.target.value)}
                    style="width:100%;"
                  ></ha-textfield>
                </div>
              `
              : isOpenAI
                ? x`
                  <div class="form-group">
                    <label>API Key</label>
                    ${
                      host._config.openai_api_key_set
                        ? x`<div class="key-hint">
                          ${host._config.openai_api_key_hint}
                        </div>`
                        : x`<div class="key-not-set">No API key set</div>`
                    }
                    <ha-textfield
                      label="${host._config.openai_api_key_set ? "Enter new key to replace" : "Enter API key"}"
                      type="password"
                      .value=${host._newApiKey}
                      @input=${(e5) => (host._newApiKey = e5.target.value)}
                      placeholder="sk-..."
                      style="margin-top:8px;width:100%;"
                    ></ha-textfield>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Model"
                      .value=${host._config.openai_model}
                      @input=${(e5) => host._updateConfig("openai_model", e5.target.value)}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                `
                : x`
                  <div class="form-group">
                    <ha-textfield
                      label="Host"
                      .value=${host._config.ollama_host}
                      @input=${(e5) => host._updateConfig("ollama_host", e5.target.value)}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Model"
                      .value=${host._config.ollama_model}
                      @input=${(e5) => host._updateConfig("ollama_model", e5.target.value)}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                `
          }
        </div>

        <details class="section-card settings-section advanced-section">
          <summary class="advanced-toggle">
            Advanced Settings
            <ha-icon
              icon="mdi:chevron-right"
              class="advanced-chevron"
              style="margin-left:auto;"
            ></ha-icon>
          </summary>
          <div class="settings-section-title" style="margin-top:20px;">
            Background Services
          </div>

          <div class="service-row">
            <label
              >Data Collector (AI Analysis)
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Periodically sends a snapshot of your home state to the
                  configured LLM to generate automation suggestions.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.collector_enabled}
              @change=${(e5) => host._updateConfig("collector_enabled", e5.target.checked)}
            ></ha-switch>
          </div>

          ${
            host._config.collector_enabled
              ? x`
                <div class="service-details">
                  <div class="form-group">
                    <label>Mode</label>
                    <select
                      class="form-select"
                      .value=${host._config.collector_mode}
                      @change=${(e5) => host._updateConfig("collector_mode", e5.target.value)}
                    >
                      <option value="continuous">Continuous</option>
                      <option value="scheduled">Scheduled Window</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Interval (seconds)"
                      type="number"
                      .value=${host._config.collector_interval}
                      @input=${(e5) =>
                        host._updateConfig(
                          "collector_interval",
                          parseInt(e5.target.value),
                        )}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                  ${
                    host._config.collector_mode === "scheduled"
                      ? x`
                        <div style="display:flex;gap:12px;">
                          <ha-textfield
                            label="Start (HH:MM)"
                            .value=${host._config.collector_start_time}
                            @input=${(e5) =>
                              host._updateConfig(
                                "collector_start_time",
                                e5.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                          <ha-textfield
                            label="End (HH:MM)"
                            .value=${host._config.collector_end_time}
                            @input=${(e5) =>
                              host._updateConfig(
                                "collector_end_time",
                                e5.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                        </div>
                      `
                      : ""
                  }
                </div>
              `
              : ""
          }

          <div class="service-row">
            <label
              >Network Discovery
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Scans your network for new devices and suggests adding them
                  to Home Assistant.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.discovery_enabled}
              @change=${(e5) => host._updateConfig("discovery_enabled", e5.target.checked)}
            ></ha-switch>
          </div>

          ${
            host._config.discovery_enabled
              ? x`
                <div class="service-details">
                  <div class="form-group">
                    <label>Mode</label>
                    <select
                      class="form-select"
                      .value=${host._config.discovery_mode}
                      @change=${(e5) => host._updateConfig("discovery_mode", e5.target.value)}
                    >
                      <option value="continuous">Continuous</option>
                      <option value="scheduled">Scheduled Window</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Interval (seconds)"
                      type="number"
                      .value=${host._config.discovery_interval}
                      @input=${(e5) =>
                        host._updateConfig(
                          "discovery_interval",
                          parseInt(e5.target.value),
                        )}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                  ${
                    host._config.discovery_mode === "scheduled"
                      ? x`
                        <div style="display:flex;gap:12px;">
                          <ha-textfield
                            label="Start (HH:MM)"
                            .value=${host._config.discovery_start_time}
                            @input=${(e5) =>
                              host._updateConfig(
                                "discovery_start_time",
                                e5.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                          <ha-textfield
                            label="End (HH:MM)"
                            .value=${host._config.discovery_end_time}
                            @input=${(e5) =>
                              host._updateConfig(
                                "discovery_end_time",
                                e5.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                        </div>
                      `
                      : ""
                  }
                </div>
              `
              : ""
          }
          <hr class="settings-separator" />

          <div class="service-row">
            <label
              >Developer Mode
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Shows advanced controls like manual pattern scanning in the
                  Suggestions tab. Useful for debugging and development.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.developer_mode}
              @change=${(e5) => host._updateConfig("developer_mode", e5.target.checked)}
            ></ha-switch>
          </div>
        </details>

        <div class="save-bar">
          <button
            class="btn btn-primary"
            @click=${host._saveConfig}
            ?disabled=${host._savingConfig}
          >
            ${host._savingConfig ? "Saving\u2026" : "Save Settings"}
          </button>
        </div>

        <div
          style="text-align:center;font-size:11px;opacity:0.35;margin-top:24px;"
        >
          Selora AI v${"0.3.1"}
        </div>
      </div>
    </div>
  `;
}

// src/panel/render-version-history.js
function renderVersionHistoryDrawer(host, a4) {
  const automationId = a4.automation_id || a4.entity_id;
  const versions = host._versions[automationId] || [];
  const loading = host._loadingVersions[automationId];
  return x`
    <div
      style="border:1px solid var(--divider-color);border-radius:8px;margin:8px 0 4px;padding:12px;background:var(--secondary-background-color);"
    >
      ${
        loading
          ? x`<div style="opacity:0.5;font-size:12px;">Loading…</div>`
          : versions.length === 0
            ? x`<div style="opacity:0.5;font-size:12px;">
              No version history yet.
            </div>`
            : x`
              <div style="position:relative;padding-left:20px;">
                <div
                  style="position:absolute;left:7px;top:0;bottom:0;width:2px;background:var(--divider-color);border-radius:2px;"
                ></div>
                ${versions.map((v2, i5) => {
                  const key = `${automationId}_${v2.version_id}`;
                  const restoring = host._restoringVersion[key];
                  const date = new Date(v2.created_at);
                  const timeAgo = relativeTime(date);
                  const isCurrent = i5 === 0;
                  return x`
                    <div
                      style="position:relative;margin-bottom:${i5 < versions.length - 1 ? "14px" : "0"};padding-left:14px;"
                    >
                      <div
                        style="position:absolute;left:-6px;top:3px;width:10px;height:10px;border-radius:50%;background:${isCurrent ? "#fbbf24" : "var(--divider-color)"};border:2px solid var(--secondary-background-color);"
                      ></div>
                      <div
                        style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;"
                      >
                        <span style="font-size:12px;font-weight:600;"
                          >v${versions.length - i5}</span
                        >
                        <span
                          style="font-size:11px;opacity:0.6;"
                          title=${date.toISOString()}
                          >${timeAgo}</span
                        >
                        ${
                          isCurrent
                            ? x`<span
                              style="font-size:10px;background:#fbbf24;color:#000;border-radius:4px;padding:1px 6px;font-weight:600;"
                              >current</span
                            >`
                            : ""
                        }
                      </div>
                      ${
                        v2.message || v2.version_message
                          ? x`<div
                            style="font-size:11px;opacity:0.6;margin-top:2px;"
                          >
                            ${v2.message || v2.version_message}
                          </div>`
                          : ""
                      }
                      <div style="display:flex;gap:6px;margin-top:6px;">
                        <button
                          class="btn btn-outline"
                          style="font-size:10px;padding:2px 7px;"
                          @click=${() => host._toggleExpandAutomation(`ver_${key}`)}
                        >
                          <ha-icon
                            icon="mdi:code-braces"
                            style="--mdc-icon-size:11px;"
                          ></ha-icon>
                          ${host._expandedAutomations[`ver_${key}`] ? "Hide" : "YAML"}
                        </button>
                        ${
                          !isCurrent
                            ? x`
                              <button
                                class="btn btn-outline"
                                style="font-size:10px;padding:2px 7px;"
                                ?disabled=${restoring || !(v2.yaml || v2.yaml_content)}
                                @click=${() =>
                                  host._restoreVersion(
                                    automationId,
                                    v2.version_id,
                                    v2.yaml || v2.yaml_content || "",
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:restore"
                                  style="--mdc-icon-size:11px;"
                                ></ha-icon>
                                ${restoring ? "Restoring\u2026" : "Restore"}
                              </button>
                            `
                            : ""
                        }
                      </div>
                      ${
                        host._expandedAutomations[`ver_${key}`]
                          ? x`<ha-code-editor
                            mode="yaml"
                            .value=${v2.yaml || v2.yaml_content || "(no YAML stored)"}
                            read-only
                            style="--code-mirror-font-size:12px;margin-top:6px;"
                          ></ha-code-editor>`
                          : ""
                      }
                    </div>
                  `;
                })}
              </div>
            `
      }
    </div>
  `;
}
function renderDiffViewer(host) {
  if (!host._diffOpen) return "";
  const automationId = host._diffAutomationId;
  const versions = host._versions[automationId] || [];
  return x`
    <div
      style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;"
      @click=${(e5) => {
        if (e5.target === e5.currentTarget) {
          host._diffOpen = false;
          host.requestUpdate();
        }
      }}
    >
      <div
        style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:760px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.4);"
      >
        <div
          style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--divider-color);"
        >
          <span style="font-weight:700;font-size:15px;">
            <ha-icon
              icon="mdi:compare"
              style="--mdc-icon-size:17px;vertical-align:middle;margin-right:6px;"
            ></ha-icon>
            Compare Versions
          </span>
          <ha-icon
            icon="mdi:close"
            style="cursor:pointer;--mdc-icon-size:20px;"
            @click=${() => {
              host._diffOpen = false;
              host.requestUpdate();
            }}
          ></ha-icon>
        </div>
        <div
          style="padding:12px 20px;border-bottom:1px solid var(--divider-color);display:flex;gap:12px;align-items:center;flex-wrap:wrap;"
        >
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:12px;opacity:0.7;">Version A (newer):</span>
            <select
              style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
              .value=${host._diffVersionA || ""}
              @change=${async (e5) => {
                host._diffVersionA = e5.target.value;
                await host._loadDiff(
                  automationId,
                  host._diffVersionA,
                  host._diffVersionB,
                );
              }}
            >
              ${versions.map(
                (v2, i5) => x`<option value=${v2.version_id}>
                    v${versions.length - i5} —
                    ${v2.message || v2.version_message || new Date(v2.created_at).toLocaleDateString()}
                  </option>`,
              )}
            </select>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:12px;opacity:0.7;">Version B (older):</span>
            <select
              style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
              .value=${host._diffVersionB || ""}
              @change=${async (e5) => {
                host._diffVersionB = e5.target.value;
                await host._loadDiff(
                  automationId,
                  host._diffVersionA,
                  host._diffVersionB,
                );
              }}
            >
              ${versions.map(
                (v2, i5) => x`<option value=${v2.version_id}>
                    v${versions.length - i5} —
                    ${v2.message || v2.version_message || new Date(v2.created_at).toLocaleDateString()}
                  </option>`,
              )}
            </select>
          </div>
        </div>
        <div style="flex:1;overflow-y:auto;padding:12px 20px;">
          ${
            host._loadingDiff
              ? x`<div style="opacity:0.5;text-align:center;padding:24px;">
                Loading diff…
              </div>`
              : host._diffResult.length === 0
                ? x`<div style="opacity:0.5;text-align:center;padding:24px;">
                  No differences found.
                </div>`
                : x`<pre
                  style="font-size:12px;margin:0;font-family:monospace;white-space:pre-wrap;"
                >
${host._diffResult.map((line) => {
  const bg = line.startsWith("+")
    ? "rgba(40,167,69,0.15)"
    : line.startsWith("-")
      ? "rgba(220,53,69,0.15)"
      : "transparent";
  const color = line.startsWith("+")
    ? "#40c057"
    : line.startsWith("-")
      ? "#fa5252"
      : "var(--primary-text-color)";
  return x`<span
                      style="display:block;background:${bg};color:${color};padding:1px 4px;"
                      >${line}</span
                    >`;
})}</pre
                >`
          }
        </div>
      </div>
    </div>
  `;
}
function renderDeletedSection(host) {
  const daysRemaining = (deletedAt) => {
    const elapsed =
      (Date.now() - new Date(deletedAt).getTime()) / (1e3 * 60 * 60 * 24);
    return Math.max(0, Math.round(30 - elapsed));
  };
  return x`
    <div style="margin-top:16px;">
      <div
        class="expand-toggle"
        style="display:flex;align-items:center;gap:6px;"
        @click=${() => host._toggleDeletedSection()}
      >
        <ha-icon
          icon="mdi:trash-can-outline"
          style="--mdc-icon-size:14px;opacity:0.6;"
        ></ha-icon>
        <span>Recently Deleted</span>
        <ha-icon
          icon="mdi:chevron-${host._showDeleted ? "up" : "down"}"
          style="--mdc-icon-size:14px;margin-left:auto;"
        ></ha-icon>
      </div>
      ${
        host._showDeleted
          ? x`
            <div style="margin-top:8px;">
              ${
                host._loadingDeleted
                  ? x`<div style="opacity:0.5;font-size:12px;padding:8px 0;">
                    Loading…
                  </div>`
                  : host._deletedAutomations.length === 0
                    ? x`<div
                      style="opacity:0.45;font-size:12px;padding:8px 0;"
                    >
                      No recently deleted automations.
                    </div>`
                    : host._deletedAutomations.map((a4) => {
                        const automationId = a4.automation_id || a4.entity_id;
                        const days = daysRemaining(a4.deleted_at);
                        const restoring =
                          host._restoringAutomation[automationId];
                        const hardDeleting =
                          host._hardDeletingAutomation[automationId];
                        return x`
                        <div
                          class="card"
                          style="opacity:0.8;border-left:3px solid var(--error-color);"
                        >
                          <div class="card-header">
                            <h3 style="flex:1;">${a4.alias}</h3>
                            ${
                              days <= 3
                                ? x`<span
                                  style="font-size:10px;background:var(--error-color);color:#fff;border-radius:4px;padding:2px 6px;"
                                  >⚠ ${days}d left</span
                                >`
                                : x`<span style="font-size:11px;opacity:0.6;"
                                  >${days} days until purge</span
                                >`
                            }
                          </div>
                          <p style="font-size:11px;opacity:0.6;margin:4px 0;">
                            Deleted ${relativeTime(new Date(a4.deleted_at))}
                          </p>
                          <div class="card-actions">
                            <button
                              class="btn btn-outline"
                              ?disabled=${restoring || hardDeleting}
                              @click=${() => host._restoreDeletedAutomation(automationId)}
                            >
                              <ha-icon
                                icon="mdi:restore"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              ${restoring ? "Restoring\u2026" : "Restore"}
                            </button>
                            <button
                              class="btn btn-outline btn-danger"
                              ?disabled=${restoring || hardDeleting}
                              @click=${() =>
                                host._openHardDeleteDialog(
                                  automationId,
                                  a4.alias,
                                )}
                            >
                              <ha-icon
                                icon="mdi:trash-can"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              ${hardDeleting ? "Deleting\u2026" : "Permanently Delete"}
                            </button>
                          </div>
                        </div>
                      `;
                      })
              }
            </div>
          `
          : ""
      }
    </div>
  `;
}
function renderHardDeleteDialog(host) {
  if (!host._hardDeleteTarget) return "";
  const { automationId, alias } = host._hardDeleteTarget;
  const hardDeleting = !!host._hardDeletingAutomation[automationId];
  const canConfirm = host._hardDeleteAliasInput === alias;
  return x`
    <div
      style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;"
      @click=${(e5) => {
        if (e5.target === e5.currentTarget && !hardDeleting) {
          host._closeHardDeleteDialog();
        }
      }}
    >
      <div
        style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:520px;padding:18px;box-shadow:0 8px 32px rgba(0,0,0,0.4);border:1px solid var(--divider-color);"
      >
        <div
          style="font-size:16px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:8px;color:var(--error-color);"
        >
          <ha-icon icon="mdi:alert-octagon"></ha-icon>
          Permanently Delete Automation
        </div>
        <p
          style="font-size:13px;opacity:0.85;margin:0 0 10px;line-height:1.45;"
        >
          This action cannot be undone. Type the automation alias to confirm
          permanent deletion.
        </p>
        <p style="font-size:12px;opacity:0.75;margin:0 0 8px;">
          Alias: <strong>${alias}</strong>
        </p>
        <ha-textfield
          .value=${host._hardDeleteAliasInput}
          @input=${(e5) => (host._hardDeleteAliasInput = e5.target.value)}
          placeholder="Type alias exactly"
          ?disabled=${hardDeleting}
          style="width:100%;"
        ></ha-textfield>
        <div
          style="display:flex;justify-content:flex-end;gap:10px;margin-top:14px;"
        >
          <button
            class="btn btn-outline"
            ?disabled=${hardDeleting}
            @click=${() => host._closeHardDeleteDialog()}
          >
            Cancel
          </button>
          <button
            class="btn btn-danger"
            ?disabled=${hardDeleting || !canConfirm}
            @click=${() => host._confirmHardDelete()}
          >
            ${hardDeleting ? "Deleting\u2026" : "Permanently Delete"}
          </button>
        </div>
      </div>
    </div>
  `;
}

// src/panel/session-actions.js
var session_actions_exports = {};
__export(session_actions_exports, {
  _checkTabParam: () => _checkTabParam,
  _confirmBulkDeleteSessions: () => _confirmBulkDeleteSessions,
  _confirmDeleteSession: () => _confirmDeleteSession,
  _deleteSession: () => _deleteSession,
  _loadSessions: () => _loadSessions,
  _newAutomationChat: () => _newAutomationChat,
  _newSession: () => _newSession,
  _onSessionTouchEnd: () => _onSessionTouchEnd,
  _onSessionTouchMove: () => _onSessionTouchMove,
  _onSessionTouchStart: () => _onSessionTouchStart,
  _openSession: () => _openSession,
  _requestBulkDeleteSessions: () => _requestBulkDeleteSessions,
  _suggestAutomationName: () => _suggestAutomationName,
  _toggleSelectAllSessions: () => _toggleSelectAllSessions,
  _toggleSessionSelection: () => _toggleSessionSelection,
});
function _checkTabParam() {
  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  if (tab === "automations" || tab === "settings") {
    this._activeTab = tab;
    this._showSidebar = false;
  }
  const newAuto = params.get("new_automation");
  if (newAuto) {
    if (this.hass) {
      this._newAutomationChat(newAuto);
    } else {
      this._pendingNewAutomation = newAuto;
    }
  }
  if (tab || newAuto) {
    const url = new URL(window.location);
    url.searchParams.delete("tab");
    url.searchParams.delete("new_automation");
    window.history.replaceState({}, "", url);
  }
}
async function _loadSessions() {
  try {
    const sessions = await this.hass.callWS({
      type: "selora_ai/get_sessions",
    });
    this._sessions = sessions || [];
    if (
      !this._activeSessionId &&
      this._sessions.length > 0 &&
      this._activeTab === "chat"
    ) {
      await this._openSession(this._sessions[0].id);
    }
  } catch (err) {
    console.error("Failed to load sessions", err);
  }
}
async function _openSession(sessionId) {
  try {
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: sessionId,
    });
    this._activeSessionId = session.id;
    this._messages = session.messages || [];
    this._activeTab = "chat";
    if (this.narrow) this._showSidebar = false;
  } catch (err) {
    console.error("Failed to open session", err);
  }
}
async function _newSession() {
  try {
    const { session_id } = await this.hass.callWS({
      type: "selora_ai/new_session",
    });
    this._activeSessionId = session_id;
    this._messages = [];
    this._activeTab = "chat";
    this._welcomeKey = (this._welcomeKey || 0) + 1;
    await this._loadSessions();
    if (this.narrow) this._showSidebar = false;
  } catch (err) {
    console.error("Failed to create session", err);
  }
}
async function _newAutomationChat(name) {
  if (!name || !name.trim()) return;
  const trimmed = name.trim();
  this._showNewAutoDialog = false;
  this.requestUpdate();
  try {
    const { session_id } = await this.hass.callWS({
      type: "selora_ai/new_session",
    });
    await Promise.all([
      this.hass
        .callWS({
          type: "selora_ai/rename_session",
          session_id,
          title: trimmed,
        })
        .catch(() => {}),
      this.hass
        .callWS({
          type: "selora_ai/create_draft",
          alias: trimmed,
          session_id,
        })
        .catch(() => {}),
    ]);
    this._activeSessionId = session_id;
    this._messages = [];
    this._input = `Create a new automation called "${trimmed}".`;
    this._activeTab = "chat";
    if (this.narrow) this._showSidebar = false;
    this.requestUpdate();
    await this.updateComplete;
    const textfield = this.shadowRoot?.querySelector("ha-textfield");
    if (textfield) textfield.focus();
    this._loadAutomations();
    this._loadSessions();
  } catch (err) {
    console.error("Failed to create automation chat session", err);
  }
}
async function _suggestAutomationName() {
  this._suggestingName = true;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/chat",
      message:
        "Suggest one short, descriptive automation name for my smart home based on my devices and current setup. Reply with ONLY the automation name, nothing else. No quotes, no explanation.",
    });
    const name = (result?.response || "").trim().replace(/^["']|["']$/g, "");
    if (name) this._newAutoName = name;
    if (result?.session_id) {
      this.hass
        .callWS({
          type: "selora_ai/delete_session",
          session_id: result.session_id,
        })
        .catch(() => {});
      this._loadSessions();
    }
  } catch (err) {
    console.error("Failed to suggest name", err);
    this._showToast(
      "Failed to generate suggestion \u2014 check LLM config",
      "error",
    );
  } finally {
    this._suggestingName = false;
  }
}
function _deleteSession(sessionId, evt) {
  evt.stopPropagation();
  this._swipedSessionId = null;
  this._deleteConfirmSessionId = sessionId;
}
function _onSessionTouchStart(e5, id) {
  const touch = e5.touches[0];
  this._touchStartX = touch.clientX;
  this._touchStartY = touch.clientY;
  this._touchSessionId = id;
  this._touchSwiping = false;
}
function _onSessionTouchMove(e5, id) {
  if (!this._touchStartX) return;
  const dx = this._touchStartX - e5.touches[0].clientX;
  const dy = Math.abs(e5.touches[0].clientY - this._touchStartY);
  if (!this._touchSwiping && dy > 10 && dy > Math.abs(dx)) {
    this._touchStartX = null;
    return;
  }
  if (dx > 10) {
    this._touchSwiping = true;
    e5.preventDefault();
    const el = e5.currentTarget;
    el.parentElement.classList.add("reveal-delete");
    const clamped = Math.min(Math.max(dx, 0), 80);
    el.style.transform = `translateX(-${clamped}px)`;
    el.style.transition = "none";
  }
}
function _onSessionTouchEnd(e5, id) {
  if (!this._touchSwiping) {
    this._touchStartX = null;
    return;
  }
  e5.preventDefault();
  const el = e5.currentTarget;
  el.style.transition = "";
  el.style.transform = "";
  const dx = this._touchStartX - e5.changedTouches[0].clientX;
  this._touchStartX = null;
  this._touchSwiping = false;
  if (dx > 40) {
    this._swipedSessionId = this._swipedSessionId === id ? null : id;
  } else {
    this._swipedSessionId =
      this._swipedSessionId === id ? null : this._swipedSessionId;
  }
}
async function _confirmDeleteSession() {
  const sessionId = this._deleteConfirmSessionId;
  if (!sessionId) return;
  this._deleteConfirmSessionId = null;
  try {
    await this.hass.callWS({
      type: "selora_ai/delete_session",
      session_id: sessionId,
    });
    if (this._activeSessionId === sessionId) {
      this._activeSessionId = null;
      this._messages = [];
    }
    await this._loadSessions();
  } catch (err) {
    console.error("Failed to delete session", err);
  }
}
function _toggleSessionSelection(sessionId) {
  this._selectedSessionIds = {
    ...this._selectedSessionIds,
    [sessionId]: !this._selectedSessionIds[sessionId],
  };
}
function _toggleSelectAllSessions() {
  const allSelected = this._sessions.every(
    (s6) => this._selectedSessionIds[s6.id],
  );
  if (allSelected) {
    this._selectedSessionIds = {};
  } else {
    const selected = {};
    this._sessions.forEach((s6) => {
      selected[s6.id] = true;
    });
    this._selectedSessionIds = selected;
  }
}
function _requestBulkDeleteSessions() {
  const count = Object.values(this._selectedSessionIds).filter(Boolean).length;
  if (count === 0) return;
  this._deleteConfirmSessionId = "__bulk__";
}
async function _confirmBulkDeleteSessions() {
  this._deleteConfirmSessionId = null;
  const ids = Object.entries(this._selectedSessionIds)
    .filter(([, v2]) => v2)
    .map(([id]) => id);
  for (const id of ids) {
    try {
      await this.hass.callWS({
        type: "selora_ai/delete_session",
        session_id: id,
      });
      if (this._activeSessionId === id) {
        this._activeSessionId = null;
        this._messages = [];
      }
    } catch (err) {
      console.error("Failed to delete session", id, err);
    }
  }
  this._selectedSessionIds = {};
  this._selectChatsMode = false;
  await this._loadSessions();
}

// src/panel/suggestion-actions.js
var suggestion_actions_exports = {};
__export(suggestion_actions_exports, {
  _acceptProactiveSuggestion: () => _acceptProactiveSuggestion,
  _dismissProactiveSuggestion: () => _dismissProactiveSuggestion,
  _loadAutomations: () => _loadAutomations,
  _loadProactiveSuggestions: () => _loadProactiveSuggestions,
  _loadSuggestions: () => _loadSuggestions,
  _snoozeProactiveSuggestion: () => _snoozeProactiveSuggestion,
  _triggerGenerateSuggestions: () => _triggerGenerateSuggestions,
  _triggerPatternScan: () => _triggerPatternScan,
});
async function _loadSuggestions() {
  try {
    const suggestions = await this.hass.callWS({
      type: "selora_ai/get_suggestions",
    });
    this._suggestions = suggestions || [];
  } catch (err) {
    console.error("Failed to load suggestions", err);
  }
}
async function _triggerGenerateSuggestions() {
  this._generatingSuggestions = true;
  try {
    const newSuggestions = await this.hass.callWS({
      type: "selora_ai/generate_suggestions",
    });
    const existingAliases = new Set(
      (this._suggestions || []).map((s6) => {
        const a4 = s6.automation || s6.automation_data || {};
        return (a4.alias || "").toLowerCase();
      }),
    );
    const added = [];
    for (const s6 of newSuggestions || []) {
      const a4 = s6.automation || s6.automation_data || {};
      const alias = (a4.alias || "").toLowerCase();
      if (!existingAliases.has(alias)) {
        added.push(s6);
        existingAliases.add(alias);
      }
    }
    this._suggestions = [...added, ...this._suggestions];
    await this._loadProactiveSuggestions();
    if (added.length > 0) {
      this._showToast(
        `Generated ${added.length} new recommendation(s)`,
        "success",
      );
    } else {
      this._showToast(
        "Analysis complete \u2014 no new suggestions at this time",
        "info",
      );
    }
  } catch (err) {
    console.error("Failed to generate suggestions", err);
    this._showToast(
      "Failed to generate suggestions: " + (err.message || "unknown error"),
      "error",
    );
  } finally {
    this._generatingSuggestions = false;
  }
}
async function _loadAutomations() {
  try {
    const automations = await this.hass.callWS({
      type: "selora_ai/get_automations",
      include_deleted: true,
    });
    this._automations = (automations || []).reverse();
    const validIds = new Set(
      this._automations.map((a4) => a4.automation_id).filter(Boolean),
    );
    this._selectedAutomationIds = Object.fromEntries(
      Object.entries(this._selectedAutomationIds || {}).filter(
        ([id, selected]) => selected && validIds.has(id),
      ),
    );
  } catch (err) {
    console.error("Failed to load automations", err);
  }
  this._loadProactiveSuggestions();
}
async function _loadProactiveSuggestions() {
  this._loadingProactive = true;
  try {
    const suggestions = await this.hass.callWS({
      type: "selora_ai/get_proactive_suggestions",
      status: "pending",
    });
    const seen = /* @__PURE__ */ new Set();
    this._proactiveSuggestions = (suggestions || []).filter((s6) => {
      const key = (s6.description || "").toLowerCase().trim();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  } catch (err) {
    console.error("Failed to load proactive suggestions", err);
    this._proactiveSuggestions = [];
  }
  this._loadingProactive = false;
}
async function _acceptProactiveSuggestion(suggestionId, editedYaml) {
  this._acceptingProactive = {
    ...this._acceptingProactive,
    [suggestionId]: true,
  };
  try {
    if (editedYaml) {
      await this.hass.callWS({
        type: "selora_ai/accept_suggestion_with_edits",
        suggestion_id: suggestionId,
        automation_yaml: editedYaml,
      });
    } else {
      await this.hass.callWS({
        type: "selora_ai/update_proactive_suggestion",
        suggestion_id: suggestionId,
        action: "accepted",
      });
    }
    this._showToast("Suggestion accepted \u2014 automation created", "success");
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [`proactive_${suggestionId}`]: true,
    };
    await this._loadAutomations();
    await new Promise((r4) => setTimeout(r4, 650));
    this._proactiveSuggestions = this._proactiveSuggestions.filter(
      (s6) => s6.suggestion_id !== suggestionId,
    );
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [`proactive_${suggestionId}`]: false,
    };
    this._highlightAndScrollToNew();
  } catch (err) {
    console.error("Failed to accept suggestion", err);
    this._showToast("Failed to accept suggestion", "error");
  }
  this._acceptingProactive = {
    ...this._acceptingProactive,
    [suggestionId]: false,
  };
}
async function _dismissProactiveSuggestion(suggestionId) {
  this._dismissingProactive = {
    ...this._dismissingProactive,
    [suggestionId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/update_proactive_suggestion",
      suggestion_id: suggestionId,
      action: "dismissed",
    });
    this._proactiveSuggestions = this._proactiveSuggestions.filter(
      (s6) => s6.suggestion_id !== suggestionId,
    );
    this._showToast("Suggestion dismissed", "info");
  } catch (err) {
    console.error("Failed to dismiss suggestion", err);
  }
  this._dismissingProactive = {
    ...this._dismissingProactive,
    [suggestionId]: false,
  };
}
async function _snoozeProactiveSuggestion(suggestionId) {
  try {
    await this.hass.callWS({
      type: "selora_ai/update_proactive_suggestion",
      suggestion_id: suggestionId,
      action: "snoozed",
    });
    this._proactiveSuggestions = this._proactiveSuggestions.filter(
      (s6) => s6.suggestion_id !== suggestionId,
    );
    this._showToast("Suggestion snoozed for 24h", "info");
  } catch (err) {
    console.error("Failed to snooze suggestion", err);
  }
}
async function _triggerPatternScan() {
  this._loadingProactive = true;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/trigger_pattern_scan",
    });
    this._showToast(
      `Scan complete \u2014 ${result.patterns_found} patterns found`,
      "success",
    );
    await this._loadProactiveSuggestions();
  } catch (err) {
    console.error("Pattern scan failed", err);
    this._showToast("Pattern scan failed", "error");
  }
  this._loadingProactive = false;
}

// src/panel/chat-actions.js
var chat_actions_exports = {};
__export(chat_actions_exports, {
  _copyMessageText: () => _copyMessageText,
  _quickStart: () => _quickStart,
  _requestScrollChat: () => _requestScrollChat,
  _sendMessage: () => _sendMessage,
  _stopStreaming: () => _stopStreaming,
});
function _quickStart(message) {
  this._input = message;
  this._sendMessage();
}
async function _sendMessage() {
  if (!this._input.trim() || this._loading) return;
  const userMsg = this._input;
  this._messages = [...this._messages, { role: "user", content: userMsg }];
  this._input = "";
  this._loading = true;
  const assistantMsg = { role: "assistant", content: "", _streaming: true };
  this._messages = [...this._messages, assistantMsg];
  try {
    const subscribePayload = {
      type: "selora_ai/chat_stream",
      message: userMsg,
    };
    if (this._activeSessionId) {
      subscribePayload.session_id = this._activeSessionId;
    }
    this._streaming = true;
    this._streamUnsub = await this.hass.connection.subscribeMessage((event) => {
      if (event.type === "token") {
        assistantMsg.content += event.text;
        this._messages = [...this._messages];
        this._loading = false;
        this._requestScrollChat();
      } else if (event.type === "done") {
        assistantMsg.content = event.response || assistantMsg.content;
        assistantMsg.automation = event.automation || null;
        assistantMsg.automation_yaml = event.automation_yaml || null;
        assistantMsg.automation_status = event.automation ? "pending" : null;
        assistantMsg.automation_message_index =
          event.automation_message_index ?? null;
        assistantMsg.refining_automation_id =
          event.refining_automation_id || null;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        this._loading = false;
        this._streaming = false;
        this._streamUnsub = null;
        if (event.validation_error) {
          this._showToast(
            `Automation validation failed: ${event.validation_error}`,
            "error",
          );
        }
        if (event.session_id) {
          if (event.session_id !== this._activeSessionId) {
            this._activeSessionId = event.session_id;
          }
          this._loadSessions();
        }
      } else if (event.type === "error") {
        assistantMsg.content =
          "Sorry, I encountered an error: " + event.message;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        this._loading = false;
        this._streaming = false;
        this._streamUnsub = null;
      }
    }, subscribePayload);
  } catch (err) {
    assistantMsg.content = "Sorry, I encountered an error: " + err.message;
    assistantMsg._streaming = false;
    this._messages = [...this._messages];
    this._loading = false;
    this._streaming = false;
    this._streamUnsub = null;
  }
}
function _stopStreaming() {
  if (this._streamUnsub) {
    this._streamUnsub();
    this._streamUnsub = null;
  }
  this._streaming = false;
  this._loading = false;
  const lastMsg = this._messages[this._messages.length - 1];
  if (lastMsg && lastMsg._streaming) {
    lastMsg._streaming = false;
    this._messages = [...this._messages];
  }
}
function _requestScrollChat() {
  if (!this._scrollPending) {
    this._scrollPending = true;
    requestAnimationFrame(() => {
      this._scrollPending = false;
      const container = this.shadowRoot.getElementById("chat-messages");
      if (container) container.scrollTop = container.scrollHeight;
    });
  }
}
async function _copyMessageText(msg, btn) {
  try {
    const text = msg.content || "";
    await navigator.clipboard.writeText(text);
    btn.classList.add("copied");
    const icon = btn.querySelector("ha-icon");
    if (icon) icon.setAttribute("icon", "mdi:check");
    setTimeout(() => {
      btn.classList.remove("copied");
      if (icon) icon.setAttribute("icon", "mdi:content-copy");
    }, 1500);
  } catch (_2) {}
}

// src/panel/automation-crud.js
var automation_crud_exports = {};
__export(automation_crud_exports, {
  _acceptAutomation: () => _acceptAutomation,
  _acceptAutomationWithEdits: () => _acceptAutomationWithEdits,
  _createAutomationFromSuggestion: () => _createAutomationFromSuggestion,
  _createSuggestionWithEdits: () => _createSuggestionWithEdits,
  _declineAutomation: () => _declineAutomation,
  _discardSuggestion: () => _discardSuggestion,
  _dismissDraft: () => _dismissDraft,
  _getRefiningAutomationId: () => _getRefiningAutomationId,
  _initYamlEdit: () => _initYamlEdit,
  _loadLineage: () => _loadLineage,
  _onYamlInput: () => _onYamlInput,
  _refineAutomation: () => _refineAutomation,
  _removeDraftForSession: () => _removeDraftForSession,
  _saveActiveAutomationYaml: () => _saveActiveAutomationYaml,
});
function _getRefiningAutomationId(msgIndex = null) {
  const msg = msgIndex == null ? null : this._messages[msgIndex];
  if (msg?.refining_automation_id) return msg.refining_automation_id;
  if (msg?.automation_id) return msg.automation_id;
  if (msg?.automation?.id) return msg.automation.id;
  for (const m2 of this._messages) {
    if (m2.automation_status === "refining") {
      if (m2.automation_id) return m2.automation_id;
      if (m2.automation?.id) return m2.automation.id;
    }
  }
  return null;
}
async function _loadLineage(automationId) {
  this._loadingLineage = { ...this._loadingLineage, [automationId]: true };
  this.requestUpdate();
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automation_lineage",
      automation_id: automationId,
    });
    this._lineage = { ...this._lineage, [automationId]: result };
  } catch (err) {
    console.error("Failed to load lineage", err);
    this._lineage = { ...this._lineage, [automationId]: [] };
  } finally {
    this._loadingLineage = { ...this._loadingLineage, [automationId]: false };
    this.requestUpdate();
  }
}
async function _acceptAutomation(msgIndex, automation) {
  try {
    const refiningId = this._getRefiningAutomationId(msgIndex);
    if (refiningId) {
      const yamlText = this._messages[msgIndex]?.automation_yaml || "";
      if (yamlText) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: yamlText,
          session_id: this._activeSessionId,
          version_message: "Refined via chat",
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation,
          session_id: this._activeSessionId,
        });
      }
    } else {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation,
        session_id: this._activeSessionId,
      });
    }
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: msgIndex,
      status: "saved",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
    await this._removeDraftForSession(this._activeSessionId);
    await this._loadAutomations();
    this._showToast(
      `Automation "${automation.alias}" ${refiningId ? "updated" : "created and enabled"}.`,
      "success",
    );
    this._activeTab = "automations";
  } catch (err) {
    this._showToast("Failed to save automation: " + err.message, "error");
  }
}
async function _removeDraftForSession(sessionId) {
  if (!sessionId) return;
  try {
    const draft = this._automations.find(
      (a4) => a4._draft && a4._linked_session === sessionId,
    );
    if (draft && draft._draft_id) {
      await this.hass.callWS({
        type: "selora_ai/remove_draft",
        draft_id: draft._draft_id,
      });
    }
  } catch (err) {
    console.error("Failed to remove draft for session", err);
  }
}
async function _dismissDraft(draftId) {
  if (!draftId) return;
  try {
    await this.hass.callWS({
      type: "selora_ai/remove_draft",
      draft_id: draftId,
    });
    await this._loadAutomations();
    this._showToast("Draft dismissed.", "info");
  } catch (err) {
    console.error("Failed to dismiss draft", err);
    this._showToast("Failed to dismiss draft: " + err.message, "error");
  }
}
async function _declineAutomation(msgIndex) {
  try {
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: msgIndex,
      status: "declined",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to decline automation", err);
  }
}
async function _refineAutomation(msgIndex, automation, description) {
  try {
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: msgIndex,
      status: "refining",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to mark automation as refining", err);
  }
  const ctx = description ? ` (${description})` : "";
  this._input = `Refine "${automation.alias}"${ctx}: `;
  this.shadowRoot.querySelector("ha-textfield")?.focus();
}
async function _createAutomationFromSuggestion(automation) {
  try {
    await this.hass.callWS({
      type: "selora_ai/create_automation",
      automation,
    });
    await this._loadAutomations();
    this._showToast(`Automation "${automation.alias}" created.`, "success");
  } catch (err) {
    this._showToast("Failed to create automation: " + err.message, "error");
  }
}
function _discardSuggestion(suggestion) {
  this._suggestions = this._suggestions.filter((s6) => s6 !== suggestion);
}
async function _acceptAutomationWithEdits(msgIndex, automation, yamlKey) {
  const edited = this._editedYaml[yamlKey];
  const msg = this._messages[msgIndex] || {};
  const originalYaml = msg.automation_yaml || "";
  const refiningId = this._getRefiningAutomationId(msgIndex);
  if (edited && edited !== (this._originalYaml?.[yamlKey] ?? originalYaml)) {
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();
      if (refiningId) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: edited,
          session_id: this._activeSessionId,
          version_message: "Refined via chat (with edits)",
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/apply_automation_yaml",
          yaml_text: edited,
          session_id: this._activeSessionId,
        });
      }
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "saved",
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId,
      });
      this._messages = session.messages || [];
      await this._loadAutomations();
      this._showToast(
        `Automation "${automation.alias}" ${refiningId ? "updated" : "created and enabled"}.`,
        "success",
      );
      this._activeTab = "automations";
    } catch (err) {
      this._showToast(
        "Failed to save automation from edited YAML: " + err.message,
        "error",
      );
    } finally {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
      this.requestUpdate();
    }
  } else {
    await this._acceptAutomation(msgIndex, automation);
  }
}
async function _createSuggestionWithEdits(auto, yamlKey, originalYaml) {
  const edited = this._editedYaml[yamlKey];
  try {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
    this.requestUpdate();
    if (edited && edited !== originalYaml) {
      await this.hass.callWS({
        type: "selora_ai/apply_automation_yaml",
        yaml_text: edited,
      });
    } else {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: auto,
      });
    }
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [yamlKey]: true,
    };
    await this._loadAutomations();
    this._showToast(`Automation "${auto.alias}" created.`, "success");
    await new Promise((r4) => setTimeout(r4, 650));
    this._suggestions = this._suggestions.filter((s6) => {
      const a4 = s6.automation || s6.automation_data;
      return `sug_${a4?.alias}` !== yamlKey;
    });
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [yamlKey]: false,
    };
    this._highlightAndScrollToNew();
  } catch (err) {
    this._showToast("Failed to create automation: " + err.message, "error");
  } finally {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
    this.requestUpdate();
  }
}
async function _saveActiveAutomationYaml(automationId, yamlKey) {
  const edited = this._editedYaml[yamlKey];
  if (!edited) return;
  try {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
    this.requestUpdate();
    await this.hass.callWS({
      type: "selora_ai/update_automation_yaml",
      automation_id: automationId,
      yaml_text: edited,
    });
    this._editedYaml = { ...this._editedYaml, [yamlKey]: void 0 };
    await this._loadAutomations();
    this._showToast("Automation YAML saved.", "success");
  } catch (err) {
    this._showToast("Failed to save changes: " + err.message, "error");
  } finally {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
    this.requestUpdate();
  }
}
function _initYamlEdit(key, originalYaml) {
  if (this._editedYaml[key] === void 0) {
    this._editedYaml = { ...this._editedYaml, [key]: originalYaml };
  }
}
function _onYamlInput(key, value) {
  this._editedYaml = { ...this._editedYaml, [key]: value };
  this.requestUpdate();
}

// src/panel/automation-management.js
var automation_management_exports = {};
__export(automation_management_exports, {
  _automationIsEnabled: () => _automationIsEnabled,
  _bulkSoftDeleteSelected: () => _bulkSoftDeleteSelected,
  _bulkToggleSelected: () => _bulkToggleSelected,
  _cancelRenameAutomation: () => _cancelRenameAutomation,
  _clearAutomationSelection: () => _clearAutomationSelection,
  _closeBurgerMenus: () => _closeBurgerMenus,
  _closeHardDeleteDialog: () => _closeHardDeleteDialog,
  _confirmHardDelete: () => _confirmHardDelete,
  _getSelectedAutomationIds: () => _getSelectedAutomationIds,
  _loadAutomationToChat: () => _loadAutomationToChat,
  _loadDeletedAutomations: () => _loadDeletedAutomations,
  _loadDiff: () => _loadDiff,
  _loadVersionHistory: () => _loadVersionHistory,
  _openDiffViewer: () => _openDiffViewer,
  _openHardDeleteDialog: () => _openHardDeleteDialog,
  _openVersionHistory: () => _openVersionHistory,
  _restoreDeletedAutomation: () => _restoreDeletedAutomation,
  _restoreVersion: () => _restoreVersion,
  _saveRenameAutomation: () => _saveRenameAutomation,
  _softDeleteAutomation: () => _softDeleteAutomation,
  _startRenameAutomation: () => _startRenameAutomation,
  _toggleAutomation: () => _toggleAutomation,
  _toggleAutomationSelection: () => _toggleAutomationSelection,
  _toggleBurgerMenu: () => _toggleBurgerMenu,
  _toggleDeletedSection: () => _toggleDeletedSection,
  _toggleExpandAutomation: () => _toggleExpandAutomation,
  _toggleSelectAllFiltered: () => _toggleSelectAllFiltered,
});
function _toggleExpandAutomation(key) {
  this._expandedAutomations = {
    ...this._expandedAutomations,
    [key]: !this._expandedAutomations[key],
  };
  this.requestUpdate();
}
function _getSelectedAutomationIds() {
  return Object.keys(this._selectedAutomationIds || {}).filter(
    (id) => this._selectedAutomationIds[id],
  );
}
function _automationIsEnabled(automation) {
  if (!automation) return false;
  if (automation.state === "on") return true;
  if (automation.state === "off") return false;
  if (
    automation.state === "unavailable" &&
    typeof automation.persisted_enabled === "boolean"
  ) {
    return automation.persisted_enabled;
  }
  return false;
}
function _toggleAutomationSelection(automationId, evt) {
  evt.stopPropagation();
  if (!automationId) return;
  const checked = !!evt.target.checked;
  this._selectedAutomationIds = {
    ...this._selectedAutomationIds,
    [automationId]: checked,
  };
  this.requestUpdate();
}
function _toggleSelectAllFiltered(filteredAutomations, checked) {
  const selectable = (filteredAutomations || []).filter(
    (a4) => !a4._draft && a4.automation_id,
  );
  const next = { ...this._selectedAutomationIds };
  for (const auto of selectable) {
    next[auto.automation_id] = checked;
  }
  this._selectedAutomationIds = next;
  this.requestUpdate();
}
function _clearAutomationSelection() {
  this._selectedAutomationIds = {};
  this.requestUpdate();
}
async function _bulkToggleSelected(enable) {
  if (this._bulkActionInProgress) return;
  const selectedIds = this._getSelectedAutomationIds();
  if (!selectedIds.length) return;
  const byId = new Map(this._automations.map((a4) => [a4.automation_id, a4]));
  const targets = selectedIds
    .map((id) => byId.get(id))
    .filter((a4) => a4 && !a4._draft && a4.automation_id)
    .filter((a4) =>
      enable ? !this._automationIsEnabled(a4) : this._automationIsEnabled(a4),
    );
  const skippedCount = selectedIds.length - targets.length;
  if (!targets.length) {
    this._showToast(
      `Selected automations are already ${enable ? "enabled" : "disabled"}.`,
      "info",
    );
    return;
  }
  this._bulkActionInProgress = true;
  this._bulkActionLabel = `${enable ? "Enabling" : "Disabling"} ${targets.length} automation(s)\u2026`;
  let successCount = 0;
  try {
    for (const auto of targets) {
      try {
        await this.hass.callWS({
          type: "selora_ai/toggle_automation",
          automation_id: auto.automation_id,
          entity_id: auto.entity_id,
          enabled: enable,
        });
        successCount += 1;
      } catch (err) {
        console.error("Bulk toggle failed", auto.automation_id, err);
      }
    }
    await this._loadAutomations();
    const failedCount = targets.length - successCount;
    if (failedCount === 0) {
      const skippedNote =
        skippedCount > 0 ? ` (${skippedCount} already in target state)` : "";
      this._showToast(
        `${enable ? "Enabled" : "Disabled"} ${successCount} automation(s)${skippedNote}.`,
        "success",
      );
    } else {
      this._showToast(
        `${enable ? "Enable" : "Disable"} completed: ${successCount} succeeded, ${failedCount} failed.`,
        "error",
      );
    }
  } finally {
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this.requestUpdate();
  }
}
async function _bulkSoftDeleteSelected() {
  if (this._bulkActionInProgress) return;
  const selectedIds = this._getSelectedAutomationIds();
  if (!selectedIds.length) return;
  const byId = new Map(this._automations.map((a4) => [a4.automation_id, a4]));
  const targets = selectedIds
    .map((id) => byId.get(id))
    .filter((a4) => a4 && !a4._draft && a4.automation_id);
  if (!targets.length) return;
  if (!confirm(`Soft-delete ${targets.length} selected automation(s)?`)) return;
  this._bulkActionInProgress = true;
  this._bulkActionLabel = `Soft-deleting ${targets.length} automation(s)\u2026`;
  let successCount = 0;
  try {
    for (const auto of targets) {
      try {
        await this.hass.callWS({
          type: "selora_ai/soft_delete_automation",
          automation_id: auto.automation_id,
        });
        successCount += 1;
      } catch (err) {
        console.error("Bulk soft-delete failed", auto.automation_id, err);
      }
    }
    this._selectedAutomationIds = {};
    await this._loadAutomations();
    const failedCount = targets.length - successCount;
    if (failedCount === 0) {
      this._showToast(`Soft-deleted ${successCount} automation(s).`, "success");
    } else {
      this._showToast(
        `Soft-delete completed: ${successCount} succeeded, ${failedCount} failed.`,
        "error",
      );
    }
  } finally {
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this.requestUpdate();
  }
}
async function _toggleAutomation(entityId, automationId, enabled) {
  try {
    await this.hass.callWS({
      type: "selora_ai/toggle_automation",
      automation_id: automationId,
      entity_id: entityId,
      enabled: !!enabled,
    });
    await this._loadAutomations();
  } catch (err) {
    console.error("Failed to toggle automation", err);
    const message = err?.message || "unknown error";
    this._showToast(`Failed to toggle automation: ${message}`, "error");
  }
}
function _toggleBurgerMenu(automationId, evt) {
  evt.stopPropagation();
  this._openBurgerMenu =
    this._openBurgerMenu === automationId ? null : automationId;
  this.requestUpdate();
}
function _closeBurgerMenus() {
  if (this._openBurgerMenu) {
    this._openBurgerMenu = null;
    this.requestUpdate();
  }
}
function _startRenameAutomation(automationId, currentAlias) {
  this._editingAlias = automationId;
  this._editingAliasValue = currentAlias || "";
  this._openBurgerMenu = null;
  this.requestUpdate();
  this.updateComplete.then(() => {
    const input = this.shadowRoot.querySelector(
      `.rename-input[data-id="${automationId}"]`,
    );
    if (input) {
      input.focus();
      input.select();
    }
  });
}
async function _saveRenameAutomation(automationId) {
  const newAlias = (this._editingAliasValue || "").trim();
  if (!newAlias) {
    this._editingAlias = null;
    return;
  }
  try {
    await this.hass.callWS({
      type: "selora_ai/rename_automation",
      automation_id: automationId,
      alias: newAlias,
    });
    this._editingAlias = null;
    this._showToast("Automation renamed", "success");
    await this._loadAutomations();
  } catch (err) {
    console.error("Failed to rename automation", err);
    this._showToast("Failed to rename: " + err.message, "error");
  }
}
function _cancelRenameAutomation() {
  this._editingAlias = null;
  this._editingAliasValue = "";
}
async function _openVersionHistory(automationId) {
  const isOpen = !!this._versionHistoryOpen[automationId];
  this._versionHistoryOpen = {
    ...this._versionHistoryOpen,
    [automationId]: !isOpen,
  };
  if (!isOpen && !this._versions[automationId]) {
    await this._loadVersionHistory(automationId);
  }
  this.requestUpdate();
}
async function _loadVersionHistory(automationId) {
  this._loadingVersions = { ...this._loadingVersions, [automationId]: true };
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automation_versions",
      automation_id: automationId,
    });
    const ordered = Array.isArray(result) ? [...result].reverse() : [];
    this._versions = { ...this._versions, [automationId]: ordered };
  } catch (err) {
    console.error("Failed to load version history", err);
    this._showToast("Failed to load version history: " + err.message, "error");
  } finally {
    this._loadingVersions = {
      ...this._loadingVersions,
      [automationId]: false,
    };
  }
  this.requestUpdate();
}
async function _openDiffViewer(automationId) {
  const versions = this._versions[automationId];
  if (!versions || versions.length < 2)
    await this._loadVersionHistory(automationId);
  const v2 = this._versions[automationId] || [];
  this._diffAutomationId = automationId;
  this._diffVersionA = v2[0]?.version_id || null;
  this._diffVersionB = v2[1]?.version_id || null;
  this._diffResult = [];
  this._diffOpen = true;
  if (this._diffVersionA && this._diffVersionB) {
    await this._loadDiff(automationId, this._diffVersionA, this._diffVersionB);
  }
  this.requestUpdate();
}
async function _loadDiff(automationId, versionAId, versionBId) {
  if (!versionAId || !versionBId) return;
  this._loadingDiff = true;
  this._diffResult = [];
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automation_diff",
      automation_id: automationId,
      version_id_a: versionAId,
      version_id_b: versionBId,
    });
    const diffText = result?.diff || "";
    this._diffResult = diffText ? diffText.split("\n") : [];
  } catch (err) {
    console.error("Failed to load diff", err);
    this._showToast("Failed to load diff: " + err.message, "error");
  } finally {
    this._loadingDiff = false;
  }
  this.requestUpdate();
}
async function _restoreVersion(automationId, versionId, yamlText) {
  const key = `${automationId}_${versionId}`;
  this._restoringVersion = { ...this._restoringVersion, [key]: true };
  try {
    await this.hass.callWS({
      type: "selora_ai/update_automation_yaml",
      automation_id: automationId,
      yaml_text: yamlText,
      version_message: `Restored from version ${versionId}`,
    });
    this._versionHistoryOpen = {
      ...this._versionHistoryOpen,
      [automationId]: false,
    };
    this._versions = { ...this._versions, [automationId]: null };
    await this._loadAutomations();
    this._showToast("Version restored.", "success");
  } catch (err) {
    console.error("Failed to restore version", err);
    this._showToast("Failed to restore version: " + err.message, "error");
  } finally {
    this._restoringVersion = { ...this._restoringVersion, [key]: false };
  }
  this.requestUpdate();
}
async function _softDeleteAutomation(automationId) {
  this._deletingAutomation = {
    ...this._deletingAutomation,
    [automationId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/soft_delete_automation",
      automation_id: automationId,
    });
    await this._loadAutomations();
    this._showToast("Automation moved to Recently Deleted.", "success");
  } catch (err) {
    console.error("Failed to delete automation", err);
    this._showToast("Failed to delete automation: " + err.message, "error");
  } finally {
    this._deletingAutomation = {
      ...this._deletingAutomation,
      [automationId]: false,
    };
  }
  this.requestUpdate();
}
async function _restoreDeletedAutomation(automationId) {
  this._restoringAutomation = {
    ...this._restoringAutomation,
    [automationId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/restore_automation",
      automation_id: automationId,
    });
    await this._loadDeletedAutomations();
    await this._loadAutomations();
    this._showToast("Automation restored.", "success");
  } catch (err) {
    console.error("Failed to restore automation", err);
    this._showToast("Failed to restore automation: " + err.message, "error");
  } finally {
    this._restoringAutomation = {
      ...this._restoringAutomation,
      [automationId]: false,
    };
  }
  this.requestUpdate();
}
function _openHardDeleteDialog(automationId, alias) {
  this._hardDeleteTarget = { automationId, alias };
  this._hardDeleteAliasInput = "";
  this.requestUpdate();
}
function _closeHardDeleteDialog() {
  this._hardDeleteTarget = null;
  this._hardDeleteAliasInput = "";
  this.requestUpdate();
}
async function _confirmHardDelete() {
  const target = this._hardDeleteTarget;
  if (!target) return;
  const { automationId, alias } = target;
  if (this._hardDeleteAliasInput !== alias) return;
  this._hardDeletingAutomation = {
    ...this._hardDeletingAutomation,
    [automationId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/hard_delete_automation",
      automation_id: automationId,
    });
    this._closeHardDeleteDialog();
    await this._loadDeletedAutomations();
    await this._loadAutomations();
    this._showToast("Automation permanently deleted.", "success");
  } catch (err) {
    console.error("Failed to hard delete automation", err);
    this._showToast(
      "Failed to permanently delete automation: " + err.message,
      "error",
    );
  } finally {
    this._hardDeletingAutomation = {
      ...this._hardDeletingAutomation,
      [automationId]: false,
    };
  }
  this.requestUpdate();
}
async function _toggleDeletedSection() {
  this._showDeleted = !this._showDeleted;
  if (this._showDeleted && this._deletedAutomations.length === 0) {
    await this._loadDeletedAutomations();
  }
  this.requestUpdate();
}
async function _loadDeletedAutomations() {
  this._loadingDeleted = true;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automations",
      include_deleted: true,
    });
    this._deletedAutomations = (result || []).filter((a4) => a4.is_deleted);
  } catch (err) {
    console.error("Failed to load deleted automations", err);
    this._showToast(
      "Failed to load deleted automations: " + err.message,
      "error",
    );
  } finally {
    this._loadingDeleted = false;
  }
  this.requestUpdate();
}
async function _loadAutomationToChat(automationId) {
  if (!automationId) {
    this._showToast(
      "This automation cannot be refined because it has no automation ID.",
      "error",
    );
    return;
  }
  this._loadingToChat = { ...this._loadingToChat, [automationId]: true };
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/load_automation_to_session",
      automation_id: automationId,
    });
    const sessionId = result?.session_id;
    if (sessionId) {
      this._activeSessionId = sessionId;
      this._activeTab = "chat";
      this._showSidebar = false;
      await this._openSession(sessionId);
      this._showToast("Automation loaded into chat.", "success");
    }
  } catch (err) {
    console.error("Failed to load automation to chat", err);
    this._showToast(
      "Failed to load automation into chat: " + err.message,
      "error",
    );
  } finally {
    this._loadingToChat = { ...this._loadingToChat, [automationId]: false };
  }
  this.requestUpdate();
}

// src/panel.js
var SeloraAIArchitectPanel = class extends s4 {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean, reflect: true },
      route: { type: Object },
      panel: { type: Object },
      // Session list
      _sessions: { type: Array },
      _activeSessionId: { type: String },
      // Message view
      _messages: { type: Array },
      _input: { type: String },
      _loading: { type: Boolean },
      _streaming: { type: Boolean },
      // Sidebar visibility (mobile)
      _showSidebar: { type: Boolean },
      // Tabs
      _activeTab: { type: String },
      // Automations tab
      _suggestions: { type: Array },
      _automations: { type: Array },
      _expandedAutomations: { type: Object },
      // Settings tab
      _config: { type: Object },
      _savingConfig: { type: Boolean },
      _newApiKey: { type: String },
      // Editable YAML state (keyed by msgIndex or suggestion key)
      _editedYaml: { type: Object },
      _savingYaml: { type: Object },
      // Version history drawer
      _versionHistoryOpen: { type: Object },
      _versions: { type: Object },
      _loadingVersions: { type: Object },
      _versionTab: { type: Object },
      // keyed by automationId → "versions" | "lineage"
      _lineage: { type: Object },
      // keyed by automationId → LineageEntry[]
      _loadingLineage: { type: Object },
      // Diff viewer
      _diffOpen: { type: Boolean },
      _diffAutomationId: { type: String },
      _diffVersionA: { type: String },
      _diffVersionB: { type: String },
      _diffResult: { type: Array },
      _loadingDiff: { type: Boolean },
      // Automation filter
      _automationFilter: { type: String },
      _statusFilter: { type: String },
      _sortBy: { type: String },
      // Suggestion filter
      _suggestionFilter: { type: String },
      _suggestionSourceFilter: { type: String },
      _suggestionSortBy: { type: String },
      // Burger menu
      _openBurgerMenu: { type: String },
      // Recently deleted section
      _showDeleted: { type: Boolean },
      _deletedAutomations: { type: Array },
      _loadingDeleted: { type: Boolean },
      // Action loading states
      _deletingAutomation: { type: Object },
      _restoringAutomation: { type: Object },
      _hardDeletingAutomation: { type: Object },
      _restoringVersion: { type: Object },
      _loadingToChat: { type: Object },
      // Bulk automation actions
      _selectedAutomationIds: { type: Object },
      _bulkActionInProgress: { type: Boolean },
      _bulkActionLabel: { type: String },
      // Hard delete confirmation modal
      _hardDeleteTarget: { type: Object },
      _hardDeleteAliasInput: { type: String },
      // Toast notifications
      _toast: { type: String },
      _toastType: { type: String },
      // Detail drawer for compact grid
      _expandedDetailId: { type: String },
      // New automation dialog
      _showNewAutoDialog: { type: Boolean },
      _newAutoName: { type: String },
      _suggestingName: { type: Boolean },
      // Generate suggestions loading
      _generatingSuggestions: { type: Boolean },
      // Suggestions visible count (incremental load)
      _suggestionsVisibleCount: { type: Number },
      // Suggestions bulk edit
      _suggestionBulkMode: { type: Boolean },
      _selectedSuggestionKeys: { type: Object },
      // Highlight newly accepted automation
      _highlightedAutomation: { type: String },
      // Fading out suggestion card keys
      _fadingOutSuggestions: { type: Object },
      // Inline card tabs (flow / yaml / history)
      _cardActiveTab: { type: Object },
      // Bulk edit mode
      _bulkEditMode: { type: Boolean },
      // Inline alias editing
      _editingAlias: { type: String },
      // automation_id being renamed
      _editingAliasValue: { type: String },
      // Proactive suggestions (pattern-based)
      _proactiveSuggestions: { type: Array },
      _loadingProactive: { type: Boolean },
      _proactiveExpanded: { type: Object },
      _acceptingProactive: { type: Object },
      _dismissingProactive: { type: Object },
      _showProactive: { type: Boolean },
      // Delete session confirmation
      _deleteConfirmSessionId: { type: String },
      // Bulk session delete
      _selectChatsMode: { type: Boolean },
      _swipedSessionId: { type: String },
      _selectedSessionIds: { type: Object },
      // Pending "Create in Chat" from dashboard card
      _pendingNewAutomation: { type: String },
      // Pagination
      _automationsPage: { type: Number },
      _suggestionsPage: { type: Number },
      _autosPerPage: { type: Number },
      _suggestionsPerPage: { type: Number },
      // Feedback modal
      _showFeedbackModal: { type: Boolean },
      _feedbackText: { type: String },
      _feedbackRating: { type: String },
      _feedbackCategory: { type: String },
      _feedbackEmail: { type: String },
      _submittingFeedback: { type: Boolean },
    };
  }
  constructor() {
    super();
    this._sessions = [];
    this._activeSessionId = null;
    this._messages = [];
    this._input = "";
    this._loading = false;
    this._streaming = false;
    this._streamUnsub = null;
    this._showSidebar = false;
    this._activeTab = "chat";
    this._suggestions = [];
    this._automations = [];
    this._expandedAutomations = {};
    this._suggestionsVisibleCount = 3;
    this._suggestionBulkMode = false;
    this._highlightedAutomation = null;
    this._fadingOutSuggestions = {};
    this._selectedSuggestionKeys = {};
    this._editedYaml = {};
    this._savingYaml = {};
    this._config = null;
    this._savingConfig = false;
    this._newApiKey = "";
    this._versionHistoryOpen = {};
    this._versions = {};
    this._loadingVersions = {};
    this._versionTab = {};
    this._lineage = {};
    this._loadingLineage = {};
    this._diffOpen = false;
    this._diffAutomationId = null;
    this._diffVersionA = null;
    this._diffVersionB = null;
    this._diffResult = [];
    this._loadingDiff = false;
    this._automationFilter = "";
    this._statusFilter = "all";
    this._sortBy = "recent";
    this._suggestionFilter = "";
    this._suggestionSourceFilter = "all";
    this._suggestionSortBy = "recent";
    this._openBurgerMenu = null;
    this._showDeleted = false;
    this._deletedAutomations = [];
    this._loadingDeleted = false;
    this._deletingAutomation = {};
    this._restoringAutomation = {};
    this._hardDeletingAutomation = {};
    this._restoringVersion = {};
    this._loadingToChat = {};
    this._selectedAutomationIds = {};
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this._hardDeleteTarget = null;
    this._hardDeleteAliasInput = "";
    this._toast = "";
    this._toastType = "info";
    this._toastTimer = null;
    this._expandedDetailId = null;
    this._showNewAutoDialog = false;
    this._newAutoName = "";
    this._suggestingName = false;
    this._generatingSuggestions = false;
    this._cardActiveTab = {};
    this._bulkEditMode = false;
    this._editingAlias = null;
    this._editingAliasValue = "";
    this._proactiveSuggestions = [];
    this._loadingProactive = false;
    this._proactiveExpanded = {};
    this._acceptingProactive = {};
    this._dismissingProactive = {};
    this._showProactive = true;
    this._deleteConfirmSessionId = null;
    this._selectChatsMode = false;
    this._selectedSessionIds = {};
    this._automationsPage = 1;
    this._suggestionsPage = 1;
    this._autosPerPage = 20;
    this._suggestionsPerPage = 10;
    this._showFeedbackModal = false;
    this._feedbackText = "";
    this._feedbackRating = "";
    this._feedbackCategory = "";
    this._feedbackEmail = "";
    this._submittingFeedback = false;
  }
  connectedCallback() {
    super.connectedCallback();
    if (!document.querySelector("link[data-selora-font]")) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href =
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap";
      link.dataset.seloraFont = "1";
      document.head.appendChild(link);
    }
    this._checkTabParam();
    this._loadSessions();
    this._loadSuggestions();
    this._loadAutomations();
    this._locationHandler = () => this._checkTabParam();
    window.addEventListener("location-changed", this._locationHandler);
    this._keyDownHandler = (e5) => {
      if (
        e5.key === "Escape" &&
        this._showFeedbackModal &&
        !this._submittingFeedback
      ) {
        this._closeFeedback();
      }
    };
    window.addEventListener("keydown", this._keyDownHandler);
  }
  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._locationHandler) {
      window.removeEventListener("location-changed", this._locationHandler);
    }
    if (this._keyDownHandler) {
      window.removeEventListener("keydown", this._keyDownHandler);
      this._keyDownHandler = null;
    }
  }
  // -------------------------------------------------------------------------
  // Config
  // -------------------------------------------------------------------------
  async _loadConfig() {
    try {
      const config = await this.hass.callWS({ type: "selora_ai/get_config" });
      this._config = config;
      this._newApiKey = "";
    } catch (err) {
      console.error("Failed to load config", err);
    }
  }
  async _saveConfig() {
    if (!this._config || this._savingConfig) return;
    this._savingConfig = true;
    try {
      const payload = { ...this._config };
      const provider = this._config.llm_provider;
      if (provider === "openai") {
        if (this._newApiKey.trim()) {
          payload.openai_api_key = this._newApiKey.trim();
        } else {
          delete payload.openai_api_key;
        }
      } else {
        if (this._newApiKey.trim()) {
          payload.anthropic_api_key = this._newApiKey.trim();
        } else {
          delete payload.anthropic_api_key;
        }
      }
      delete payload.anthropic_api_key_hint;
      delete payload.anthropic_api_key_set;
      delete payload.openai_api_key_hint;
      delete payload.openai_api_key_set;
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      this._newApiKey = "";
      await this._loadConfig();
      this._showToast("Configuration saved.", "success");
    } catch (err) {
      this._showToast("Failed to save configuration: " + err.message, "error");
    } finally {
      this._savingConfig = false;
    }
  }
  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig();
  }
  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }
  _highlightAndScrollToNew() {
    const newest = this._automations[0];
    if (!newest) return;
    this._highlightedAutomation = newest.entity_id;
    this.requestUpdate();
    requestAnimationFrame(() => {
      const row = this.shadowRoot.querySelector(
        `.auto-row[data-entity-id="${newest.entity_id}"]`,
      );
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
    setTimeout(() => {
      this._highlightedAutomation = null;
    }, 3e3);
  }
  // -------------------------------------------------------------------------
  // Toast notifications
  // -------------------------------------------------------------------------
  _showToast(message, type = "info") {
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
    this._toast = message;
    this._toastType = type;
    this._toastTimer = setTimeout(() => {
      this._toast = "";
      this._toastType = "info";
      this._toastTimer = null;
      this.requestUpdate();
    }, 3500);
    this.requestUpdate();
  }
  _dismissToast() {
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
    this._toast = "";
    this._toastType = "info";
    this.requestUpdate();
  }
  _t(key, fallback) {
    return (
      this.hass?.localize?.(`component.selora_ai.common.${key}`) || fallback
    );
  }
  _openFeedback() {
    this._showFeedbackModal = true;
  }
  _closeFeedback() {
    if (this._submittingFeedback) return;
    this._showFeedbackModal = false;
    this._feedbackText = "";
    this._feedbackRating = "";
    this._feedbackCategory = "";
    this._feedbackEmail = "";
  }
  async _submitFeedback() {
    if (this._submittingFeedback) return;
    const text = (this._feedbackText || "").trim();
    if (text.length < 10) {
      this._showToast(
        this._t(
          "feedback_min_length_error",
          "Please enter at least 10 characters.",
        ),
        "error",
      );
      return;
    }
    this._submittingFeedback = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1e4);
    try {
      const payload = {
        message: text,
        ha_version: this.hass?.config?.version || "unknown",
        integration_version: true ? "0.3.1" : "unknown",
      };
      if (this._feedbackRating) payload.rating = this._feedbackRating;
      if (this._feedbackCategory) payload.category = this._feedbackCategory;
      const email = (this._feedbackEmail || "").trim();
      if (email) payload.email = email;
      const res = await fetch(
        "https://qiob98god6.execute-api.us-east-1.amazonaws.com/api/feedback",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this._showToast(
        this._t("feedback_success", "Thanks for your feedback!"),
        "success",
      );
      this._showFeedbackModal = false;
      this._feedbackText = "";
      this._feedbackRating = "";
      this._feedbackCategory = "";
      this._feedbackEmail = "";
    } catch (err) {
      this._showToast(
        err?.message ||
          this._t(
            "feedback_error",
            "Couldn\u2019t send feedback \u2014 please try again.",
          ),
        "error",
      );
    } finally {
      clearTimeout(timeout);
      this._submittingFeedback = false;
    }
  }
  // -------------------------------------------------------------------------
  // Scroll to bottom on new messages
  // -------------------------------------------------------------------------
  updated(changedProps) {
    if (changedProps.has("hass")) {
      this._checkTabParam();
      const dark = this.hass?.themes?.darkMode;
      if (dark !== void 0) {
        this.style.setProperty(
          "--selora-accent-text",
          dark ? "#fbbf24" : "#18181b",
        );
      }
    }
    if (this.hass && this._pendingNewAutomation) {
      const name = this._pendingNewAutomation;
      this._pendingNewAutomation = null;
      this._newAutomationChat(name);
    }
    if (changedProps.has("_messages") && this._activeTab === "chat") {
      const container = this.shadowRoot.getElementById("chat-messages");
      if (container) container.scrollTop = container.scrollHeight;
    }
  }
  // -------------------------------------------------------------------------
  // Styles
  // -------------------------------------------------------------------------
  static get styles() {
    return [
      seloraTokens,
      sharedAnimations,
      sharedButtons,
      sharedModals,
      sharedBadges,
      sharedLoaders,
      sharedScrollbar,
      ...allPanelStyles,
    ];
  }
  // -------------------------------------------------------------------------
  // Render delegation wrappers
  // -------------------------------------------------------------------------
  _renderNewAutomationDialog() {
    return renderNewAutomationDialog(this);
  }
  _renderChat() {
    return renderChat(this);
  }
  _renderMessage(msg, idx) {
    return renderMessage(this, msg, idx);
  }
  _renderYamlEditor(key, originalYaml, onSave) {
    return renderYamlEditor(this, key, originalYaml, onSave);
  }
  _renderAutomationFlowchart(auto) {
    return renderAutomationFlowchart(this, auto);
  }
  _renderProposalCard(msg, msgIndex) {
    return renderProposalCard(this, msg, msgIndex);
  }
  _toggleYaml(msgIndex) {
    return toggleYaml(this, msgIndex);
  }
  _masonryColumns(cards, cols, firstColFooter) {
    return masonryColumns(cards, cols, firstColFooter);
  }
  _renderAutomations() {
    return renderAutomations(this);
  }
  _renderSuggestionsSection() {
    return renderSuggestionsSection(this);
  }
  _renderSettings() {
    return renderSettings(this);
  }
  _renderVersionHistoryDrawer(a4) {
    return renderVersionHistoryDrawer(this, a4);
  }
  _renderDiffViewer() {
    return renderDiffViewer(this);
  }
  _renderDeletedSection() {
    return renderDeletedSection(this);
  }
  _renderHardDeleteDialog() {
    return renderHardDeleteDialog(this);
  }
  _renderFeedbackModal() {
    if (!this._showFeedbackModal) return "";
    const textLength = (this._feedbackText || "").length;
    const tooShort = (this._feedbackText || "").trim().length < 10;
    const ratingOptions = [
      {
        value: "thumbsup",
        icon: "mdi:thumb-up-outline",
        label: this._t("feedback_rating_thumbsup", "Thumbs up"),
      },
      {
        value: "thumbsdown",
        icon: "mdi:thumb-down-outline",
        label: this._t("feedback_rating_thumbsdown", "Thumbs down"),
      },
    ];
    const categoryOptions = [
      {
        value: "bug",
        label: this._t("feedback_category_bug", "Bug"),
      },
      {
        value: "feature",
        label: this._t("feedback_category_feature", "Feature Request"),
      },
      {
        value: "general",
        label: this._t("feedback_category_general", "General"),
      },
    ];
    return x`
      <div
        class="modal-overlay"
        @click=${(e5) => {
          if (e5.target === e5.currentTarget) this._closeFeedback();
        }}
      >
        <div
          class="modal-content"
          role="dialog"
          aria-modal="true"
          @keydown=${(e5) => {
            if (e5.key === "Enter" && e5.target.tagName !== "TEXTAREA") {
              e5.preventDefault();
              this._submitFeedback();
            }
          }}
          aria-labelledby="selora-feedback-title"
          style="max-width:520px;"
        >
          <div
            id="selora-feedback-title"
            style="font-size:18px;font-weight:600;margin-bottom:8px;"
          >
            ${this._t("feedback_modal_title", "Share Feedback")}
          </div>
          <div style="font-size:12px;opacity:0.7;margin-bottom:14px;">
            ${this._t(
              "feedback_privacy_notice",
              "Feedback is anonymous and contains no personal data.",
            )}
          </div>

          <textarea
            maxlength="2000"
            style="width:100%;min-height:120px;resize:vertical;padding:10px 12px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font:inherit;box-sizing:border-box;margin-bottom:6px;"
            placeholder=${this._t(
              "feedback_textarea_placeholder",
              "What's on your mind? (10 characters minimum)",
            )}
            .value=${this._feedbackText}
            @input=${(e5) => {
              this._feedbackText = e5.target.value;
            }}
          ></textarea>
          <div
            style="font-size:11px;opacity:0.6;text-align:right;margin-bottom:12px;"
          >
            ${textLength}/2000
          </div>

          <div style="font-size:12px;opacity:0.7;margin-bottom:6px;">
            ${this._t("feedback_rating_label", "Rating:")}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
            ${ratingOptions.map(
              (opt) => x`
                <button
                  class="btn btn-outline"
                  style="padding:6px 10px;${this._feedbackRating === opt.value ? "border-color:var(--selora-accent);color:var(--selora-accent);background:rgba(251,191,36,0.08);" : ""}"
                  aria-pressed=${this._feedbackRating === opt.value ? "true" : "false"}
                  title=${opt.label}
                  @click=${() => {
                    this._feedbackRating =
                      this._feedbackRating === opt.value ? "" : opt.value;
                  }}
                >
                  <ha-icon
                    icon=${opt.icon}
                    style="--mdc-icon-size:18px;"
                  ></ha-icon>
                </button>
              `,
            )}
          </div>

          <div style="font-size:12px;opacity:0.7;margin-bottom:6px;">
            ${this._t("feedback_category_label", "Category (optional):")}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">
            ${categoryOptions.map(
              (opt) => x`
                <button
                  class="btn btn-outline"
                  style="padding:6px 10px;${this._feedbackCategory === opt.value ? "border-color:var(--selora-accent);color:var(--selora-accent);background:rgba(251,191,36,0.08);" : ""}"
                  aria-pressed=${this._feedbackCategory === opt.value ? "true" : "false"}
                  @click=${() => {
                    this._feedbackCategory =
                      this._feedbackCategory === opt.value ? "" : opt.value;
                  }}
                >
                  ${opt.label}
                </button>
              `,
            )}
          </div>

          <div style="margin-bottom:14px;">
            <div style="font-size:12px;opacity:0.7;margin-bottom:6px;">
              ${this._t("feedback_email_label", "Email (optional):")}
            </div>
            <input
              type="email"
              style="width:100%;box-sizing:border-box;padding:8px 12px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font:inherit;font-size:13px;"
              placeholder=${this._t(
                "feedback_email_placeholder",
                "your@email.com \u2014 only if you'd like a reply",
              )}
              .value=${this._feedbackEmail}
              @input=${(e5) => {
                this._feedbackEmail = e5.target.value;
              }}
            />
          </div>

          <div style="display:flex;justify-content:flex-end;gap:8px;">
            <button
              class="btn btn-outline"
              ?disabled=${this._submittingFeedback}
              @click=${() => this._closeFeedback()}
            >
              ${this._t("feedback_cancel", "Cancel")}
            </button>
            <button
              class="btn btn-primary"
              ?disabled=${this._submittingFeedback || tooShort}
              @click=${() => this._submitFeedback()}
            >
              ${this._submittingFeedback ? this._t("feedback_submitting", "Sending\u2026") : this._t("feedback_submit", "Send Feedback")}
            </button>
          </div>
        </div>
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  render() {
    return x`
      <div class="header">
        <div class="header-top">
          <img
            src="/api/selora_ai/logo.png"
            alt="Selora"
            style="width:28px;height:28px;border-radius:6px;"
          />
          <span class="gold-text">Selora AI</span>
          <button class="feedback-link" @click=${() => this._openFeedback()}>
            ${this._t("feedback_button_label", "Give Feedback")}
          </button>
        </div>
        <div class="tabs">
          <div
            class="tab ${this._activeTab === "chat" ? "active" : ""}"
            @click=${() => {
              if (this._activeTab === "chat") {
                this._showSidebar = !this._showSidebar;
              } else {
                this._activeTab = "chat";
                this._showSidebar = true;
              }
            }}
          >
            <span class="tab-inner"
              ><ha-icon icon="mdi:chat-outline" class="tab-icon"></ha-icon
              ><span class="tab-text">Chat</span></span
            >
          </div>
          <div
            class="tab ${this._activeTab === "automations" ? "active" : ""}"
            @click=${() => {
              this._activeTab = "automations";
              this._showSidebar = false;
              this._loadAutomations();
            }}
          >
            <span class="tab-inner"
              ><ha-icon icon="mdi:robot-outline" class="tab-icon"></ha-icon
              ><span class="tab-text">Automations</span></span
            >
          </div>
          <div
            class="tab ${this._activeTab === "settings" ? "active" : ""}"
            @click=${() => {
              this._activeTab = "settings";
              this._showSidebar = false;
              this._loadConfig();
            }}
          >
            <span class="tab-inner"
              ><ha-icon icon="mdi:cog-outline" class="tab-icon"></ha-icon
              ><span class="tab-text">Settings</span></span
            >
          </div>
        </div>
      </div>

      <div class="body">
        <div class="sidebar ${this._showSidebar ? "open" : ""}" part="sidebar">
          <div class="sidebar-header">
            <span>Conversations</span>
            <div
              style="display:flex;align-items:center;gap:6px;margin-left:auto;"
            >
              ${
                this._sessions.length > 0
                  ? x`
                    ${
                      this._selectChatsMode
                        ? x`
                          <button
                            class="sidebar-select-btn"
                            @click=${() => {
                              this._selectChatsMode = false;
                              this._selectedSessionIds = {};
                            }}
                          >
                            Done
                          </button>
                        `
                        : x`
                          <button
                            class="sidebar-select-btn"
                            @click=${() => {
                              this._selectChatsMode = true;
                            }}
                          >
                            Select
                          </button>
                        `
                    }
                  `
                  : ""
              }
              <ha-icon
                icon="mdi:close"
                style="--mdc-icon-size:18px;cursor:pointer;opacity:0.6;"
                @click=${() => (this._showSidebar = false)}
              ></ha-icon>
            </div>
          </div>
          ${
            this._selectChatsMode
              ? x`
                <div class="select-actions-bar">
                  <label
                    class="select-all-label"
                    @click=${() => this._toggleSelectAllSessions()}
                  >
                    <input
                      type="checkbox"
                      .checked=${
                        this._sessions.length > 0 &&
                        this._sessions.every(
                          (s6) => this._selectedSessionIds[s6.id],
                        )
                      }
                    />
                    <span>Select all</span>
                  </label>
                  <button
                    class="btn-delete-selected"
                    ?disabled=${
                      Object.values(this._selectedSessionIds).filter(Boolean)
                        .length === 0
                    }
                    @click=${() => this._requestBulkDeleteSessions()}
                  >
                    <ha-icon
                      icon="mdi:delete-outline"
                      style="--mdc-icon-size:14px;"
                    ></ha-icon>
                    Delete
                    (${Object.values(this._selectedSessionIds).filter(Boolean).length})
                  </button>
                </div>
              `
              : x`
                <button
                  class="btn btn-primary new-chat-btn"
                  style="width:calc(100% - 24px);"
                  @click=${this._newSession}
                >
                  <ha-icon
                    icon="mdi:plus"
                    style="--mdc-icon-size:16px;"
                  ></ha-icon>
                  New Chat
                </button>
              `
          }
          <div class="session-list">
            ${
              this._sessions.length === 0
                ? x`<div style="padding: 16px; font-size: 12px; opacity: 0.5;">
                  No conversations yet.
                </div>`
                : this._sessions.map(
                    (s6) => x`
                    <div
                      class="session-item-wrapper ${this._swipedSessionId === s6.id ? "reveal-delete" : ""}"
                    >
                      <div
                        class="session-item-delete-bg"
                        @click=${(e5) => this._deleteSession(s6.id, e5)}
                      >
                        <ha-icon icon="mdi:delete-outline"></ha-icon>
                      </div>
                      <div
                        class="session-item ${s6.id === this._activeSessionId ? "active" : ""} ${this._swipedSessionId === s6.id ? "swiped" : ""}"
                        @click=${() => {
                          if (this._swipedSessionId === s6.id) {
                            this._swipedSessionId = null;
                            return;
                          }
                          this._selectChatsMode
                            ? this._toggleSessionSelection(s6.id)
                            : this._openSession(s6.id);
                        }}
                        @touchstart=${(e5) => this._onSessionTouchStart(e5, s6.id)}
                        @touchmove=${(e5) => this._onSessionTouchMove(e5, s6.id)}
                        @touchend=${(e5) => this._onSessionTouchEnd(e5, s6.id)}
                      >
                        ${
                          this._selectChatsMode
                            ? x`
                              <input
                                type="checkbox"
                                class="session-checkbox"
                                .checked=${!!this._selectedSessionIds[s6.id]}
                                @click=${(e5) => {
                                  e5.stopPropagation();
                                  this._toggleSessionSelection(s6.id);
                                }}
                              />
                            `
                            : ""
                        }
                        <div style="flex:1; min-width:0;">
                          <div class="session-title">${s6.title}</div>
                          <div class="session-meta">
                            ${formatDate(s6.updated_at)}
                          </div>
                        </div>
                        ${
                          !this._selectChatsMode
                            ? x`
                              <ha-icon
                                class="session-delete"
                                icon="mdi:delete-outline"
                                @click=${(e5) => this._deleteSession(s6.id, e5)}
                                title="Delete"
                              ></ha-icon>
                            `
                            : ""
                        }
                      </div>
                    </div>
                  `,
                  )
            }
          </div>
        </div>

        <div
          class="main"
          @click=${() => {
            if (this.narrow && this._showSidebar) this._showSidebar = false;
          }}
        >
          ${this._activeTab === "chat" ? this._renderChat() : ""}
          ${this._activeTab === "automations" ? this._renderAutomations() : ""}
          ${this._activeTab === "settings" ? this._renderSettings() : ""}
        </div>
      </div>

      ${this._renderFeedbackModal()} ${this._renderHardDeleteDialog()}
      ${
        this._deleteConfirmSessionId
          ? x`
            <div
              class="modal-overlay"
              @click=${(e5) => {
                if (e5.target === e5.currentTarget)
                  this._deleteConfirmSessionId = null;
              }}
            >
              <div
                class="modal-content"
                style="max-width:400px;text-align:center;"
              >
                ${
                  this._deleteConfirmSessionId === "__bulk__"
                    ? x`
                      <div
                        style="font-size:17px;font-weight:600;margin-bottom:8px;"
                      >
                        Delete Conversations
                      </div>
                      <div
                        style="font-size:13px;opacity:0.7;margin-bottom:20px;"
                      >
                        Delete
                        ${
                          Object.values(this._selectedSessionIds).filter(
                            Boolean,
                          ).length
                        }
                        selected conversation(s)? This cannot be undone.
                      </div>
                      <div
                        style="display:flex;gap:10px;justify-content:center;"
                      >
                        <button
                          class="btn btn-outline"
                          @click=${() => {
                            this._deleteConfirmSessionId = null;
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          class="btn"
                          style="background:#ef4444;color:#fff;border-color:#ef4444;"
                          @click=${() => this._confirmBulkDeleteSessions()}
                        >
                          Delete
                        </button>
                      </div>
                    `
                    : x`
                      <div
                        style="font-size:17px;font-weight:600;margin-bottom:8px;"
                      >
                        Delete Conversation
                      </div>
                      <div
                        style="font-size:13px;opacity:0.7;margin-bottom:20px;"
                      >
                        Are you sure you want to delete this conversation? This
                        cannot be undone.
                      </div>
                      <div
                        style="display:flex;gap:10px;justify-content:center;"
                      >
                        <button
                          class="btn btn-outline"
                          @click=${() => {
                            this._deleteConfirmSessionId = null;
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          class="btn"
                          style="background:#ef4444;color:#fff;border-color:#ef4444;"
                          @click=${() => this._confirmDeleteSession()}
                        >
                          Delete
                        </button>
                      </div>
                    `
                }
              </div>
            </div>
          `
          : ""
      }
      ${
        this._toast
          ? x`
            <div class="toast ${this._toastType}">
              <span>${this._toast}</span>
              <ha-icon
                class="toast-close"
                icon="mdi:close"
                @click=${() => this._dismissToast()}
              ></ha-icon>
            </div>
          `
          : ""
      }
    `;
  }
};
Object.assign(SeloraAIArchitectPanel.prototype, session_actions_exports);
Object.assign(SeloraAIArchitectPanel.prototype, suggestion_actions_exports);
Object.assign(SeloraAIArchitectPanel.prototype, chat_actions_exports);
Object.assign(SeloraAIArchitectPanel.prototype, automation_crud_exports);
Object.assign(SeloraAIArchitectPanel.prototype, automation_management_exports);
customElements.define("selora-ai-architect", SeloraAIArchitectPanel);
/*! Bundled license information:

@lit/reactive-element/css-tag.js:
  (**
   * @license
   * Copyright 2019 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

@lit/reactive-element/reactive-element.js:
lit-html/lit-html.js:
lit-element/lit-element.js:
lit-html/directive.js:
  (**
   * @license
   * Copyright 2017 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/is-server.js:
  (**
   * @license
   * Copyright 2022 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/directive-helpers.js:
  (**
   * @license
   * Copyright 2020 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/directives/keyed.js:
  (**
   * @license
   * Copyright 2021 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)
*/
