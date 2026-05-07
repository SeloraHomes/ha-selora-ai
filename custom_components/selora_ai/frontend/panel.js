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
  /* Brand-accented pill for primary "create new" actions. Translucent
     gold fill + subtle gold border, matching the header tab visual
     language so the button feels native to the rest of the UI rather
     than the loud filled btn-primary. */
  .btn-accent {
    border-color: rgba(251, 191, 36, 0.35);
    color: var(--selora-accent-text);
    background: rgba(251, 191, 36, 0.08);
    border-radius: 999px;
    padding: 8px 16px;
    font-weight: 500;
  }
  .btn-accent:hover {
    background: rgba(251, 191, 36, 0.14);
    border-color: var(--selora-accent);
    box-shadow: none;
    opacity: 1;
  }
  /* Light mode: translucent gold-on-white is near-invisible. Match the
     filled btn-primary language (solid gold + black text) so it lines up
     with the Accept buttons elsewhere in the UI. Pill radius is kept. */
  :host(:not([dark])) .btn-accent {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #000;
  }
  :host(:not([dark])) .btn-accent:hover {
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
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
    position: relative;
  }

  /* ---- Particle band under header ---- */
  .main > selora-particles {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 220px;
    z-index: 0;
    opacity: 0;
    transition: opacity 1s ease;
    pointer-events: none;
    touch-action: none;
    mask-image: radial-gradient(
      ellipse 70% 90% at top center,
      black 10%,
      transparent 70%
    );
    -webkit-mask-image: radial-gradient(
      ellipse 70% 90% at top center,
      black 10%,
      transparent 70%
    );
  }
  .main > selora-particles.visible {
    opacity: 1;
  }
  /* Hide all particle layers (background + welcome composer) when no
     LLM provider is configured — keeps the pre-setup screen calm. */
  :host([needs-setup]) selora-particles {
    display: none !important;
  }

  /* ---- Scroll view (automations / settings) ---- */
  .scroll-view {
    flex: 1;
    overflow-y: auto;
    padding: 24px 28px;
    max-width: 1200px;
    margin: 0 auto;
    position: relative;
    z-index: 1;
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
    margin-bottom: 16px;
  }
  .section-card-header h3 {
    font-size: 20px;
    margin: 0;
    font-weight: 700;
    line-height: 1.2;
  }
  .section-card-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin: 0 0 24px;
    line-height: 1.5;
  }
  /* When a subtitle directly follows a section header, tighten the gap
     to a deliberate 8px (header has 16px margin-bottom, subtitle pulls
     back 8px) so the heading and its description read as one block. */
  .section-card-header + .section-card-subtitle {
    margin-top: -8px;
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
  .toast.warning {
    background: #b45309;
  }
  .toast-close {
    margin-left: auto;
    cursor: pointer;
    opacity: 0.85;
  }
  .toast-close:hover {
    opacity: 1;
  }

  /* ---- Quota / 429 banner ---- */
  /* Sits above the active tab content, below the header. Red to match
     the alert particles. Auto-dismisses when retry_after elapses. */
  .quota-banner {
    position: relative;
    z-index: 5;
    margin: 12px 28px 0;
    padding: 10px 14px;
    border-radius: 10px;
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.4);
    color: var(--primary-text-color);
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    line-height: 1.45;
    animation: quota-banner-in 240ms ease-out;
  }
  .quota-banner ha-icon {
    --mdc-icon-size: 20px;
    color: #ef4444;
    flex-shrink: 0;
  }
  .quota-banner-text {
    flex: 1;
    min-width: 0;
  }
  .quota-banner-close {
    background: none;
    border: none;
    cursor: pointer;
    padding: 4px;
    border-radius: 50%;
    color: var(--secondary-text-color);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .quota-banner-close:hover {
    background: rgba(0, 0, 0, 0.08);
    color: var(--primary-text-color);
  }
  @keyframes quota-banner-in {
    from {
      transform: translateY(-8px);
      opacity: 0;
    }
    to {
      transform: none;
      opacity: 1;
    }
  }
  @media (max-width: 600px) {
    .quota-banner {
      margin: 8px 10px 0;
      padding: 8px 10px;
    }
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
  .session-delete-confirm {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 10px;
    min-height: 52px;
    background: rgba(239, 68, 68, 0.06);
    border-left: 2px solid rgba(239, 68, 68, 0.4);
  }
  .session-delete-confirm-label {
    font-size: 12px;
    opacity: 0.7;
    white-space: nowrap;
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
    background: var(--app-header-background-color);
    border-bottom: var(--app-header-border-bottom, none);
    /* Must outrank the narrow-mode conversations drawer (z-index: 10
       in layout.css.js). The header creates a stacking context, so the
       overflow menu rendered inside it inherits this ceiling — without
       this bump the menu reopens hidden behind the drawer on mobile. */
    z-index: 11;
    flex-shrink: 0;
    height: var(--header-height, 56px);
    box-sizing: border-box;
    position: relative;
  }
  /* Suppress decorative glow when no LLM is configured — keeps the
     pre-setup screen calm. */
  :host([needs-setup]) .header::after,
  :host([needs-setup]) .header::before {
    display: none;
  }
  /* Golden glow line at bottom of header (dark mode only) */
  :host([dark]) .header::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: linear-gradient(
      90deg,
      transparent 5%,
      #f59e0b 50%,
      transparent 95%
    );
    z-index: 3;
  }
  :host([dark]) .header::before {
    content: "";
    position: absolute;
    bottom: -24px;
    left: 10%;
    right: 10%;
    height: 24px;
    background: radial-gradient(
      ellipse 40% 100% at center top,
      rgba(251, 191, 36, 0.6) 0%,
      rgba(245, 158, 11, 0.3) 30%,
      rgba(245, 158, 11, 0.08) 60%,
      transparent 100%
    );
    filter: blur(4px);
    z-index: 3;
  }
  .header-toolbar {
    position: relative;
    display: flex;
    align-items: center;
    height: var(--header-height, 56px);
    padding: 0 12px;
    box-sizing: border-box;
    width: 100%;
    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color: var(--app-header-text-color, var(--primary-text-color));
    -webkit-font-smoothing: var(--ha-font-smoothing, antialiased);
    -moz-osx-font-smoothing: var(--ha-moz-osx-font-smoothing, grayscale);
  }
  .menu-btn {
    background: none;
    border: none;
    cursor: pointer;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
    --mdc-icon-size: 24px;
    flex-shrink: 0;
  }
  .header-logo {
    width: 22px;
    height: 22px;
    margin-inline-start: var(--ha-space-6, 24px);
    flex-shrink: 0;
  }
  .header-title {
    margin-left: 10px;
    margin-right: 12px;
    flex-shrink: 0;
    white-space: nowrap;
    font-size: var(--ha-font-size-xl, 20px);
    font-weight: var(--ha-font-weight-normal, 400);
  }
  .tabs-center {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 6px;
    height: 100%;
    pointer-events: auto;
  }
  /* Narrow layout: hide centered tabs, they live in the Selora menu instead */
  :host([narrow]) .tabs-center {
    display: none;
  }
  :host([narrow]) .header-logo {
    margin-inline-start: 4px;
    width: 20px;
    height: 20px;
  }
  :host([narrow]) .header-title {
    font-size: var(--ha-font-size-l, 18px);
    margin-left: 8px;
  }
  .tab {
    position: relative;
    padding: 8px 16px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--app-header-text-color, var(--primary-text-color));
    opacity: 0.55;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    transition:
      opacity 0.25s,
      background 0.25s,
      border-color 0.25s,
      color 0.25s;
    white-space: nowrap;
    user-select: none;
    display: flex;
    align-items: center;
  }
  .tab:hover {
    opacity: 0.85;
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.12);
  }
  .tab.active {
    opacity: 1;
    font-weight: 600;
    color: var(--selora-accent-text);
    background: rgba(251, 191, 36, 0.1);
    border-color: rgba(251, 191, 36, 0.25);
  }
  /* Light mode */
  :host(:not([dark])) .tab {
    background: rgba(0, 0, 0, 0.05);
    border-color: rgba(0, 0, 0, 0.1);
  }
  :host(:not([dark])) .tab:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.15);
  }
  :host(:not([dark])) .tab.active {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.2);
  }
  .tab-inner {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .tab-icon {
    --mdc-icon-size: 16px;
  }
  .header-spacer {
    flex: 1;
  }
  /* New-chat header button (visible when there's a chat to leave behind).
     Mobile: icon-only circle. Desktop: pill with icon + "New chat" label. */
  .header-new-chat {
    background: none;
    border: none;
    cursor: pointer;
    height: 40px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 0 14px;
    flex-shrink: 0;
    width: auto;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
    --mdc-icon-size: 20px;
    transition:
      background 0.2s,
      color 0.2s;
  }
  .header-new-chat:hover {
    background: rgba(251, 191, 36, 0.12);
    color: var(--selora-accent-text, #f59e0b);
  }
  :host(:not([dark])) .header-new-chat:hover {
    background: rgba(0, 0, 0, 0.06);
    color: var(--primary-text-color);
  }
  .header-new-chat-label {
    white-space: nowrap;
  }
  /* Narrow: collapse back to a 44×44 icon-only circle */
  :host([narrow]) .header-new-chat {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    padding: 0;
    --mdc-icon-size: 22px;
  }
  :host([narrow]) .header-new-chat-label {
    display: none;
  }

  /* Selora (right-side) menu — gold-accented to differentiate from HA burger */
  .overflow-btn-wrap {
    position: relative;
  }
  .overflow-btn {
    background: none;
    border: none;
    cursor: pointer;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
    --mdc-icon-size: 22px;
    transition:
      background 0.2s,
      color 0.2s,
      box-shadow 0.2s;
  }
  .selora-menu-btn {
    color: var(--selora-accent-text, #f59e0b);
  }
  .selora-menu-btn:hover {
    background: rgba(251, 191, 36, 0.12);
    box-shadow: 0 0 14px rgba(251, 191, 36, 0.25);
  }
  :host(:not([dark])) .selora-menu-btn {
    color: var(--primary-text-color);
  }
  :host(:not([dark])) .selora-menu-btn:hover {
    background: rgba(0, 0, 0, 0.06);
    box-shadow: none;
  }
  .overflow-menu {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    min-width: 220px;
    background: var(--card-background-color, #fff);
    border-radius: 12px;
    box-shadow:
      0 8px 24px rgba(0, 0, 0, 0.35),
      0 2px 8px rgba(0, 0, 0, 0.18);
    padding: 6px;
    /* Sit above the narrow-mode conversations drawer (z-index: 10);
       otherwise the menu reopens behind the sidebar after navigating
       to Conversations on mobile and is invisible. */
    z-index: 20;
  }
  .selora-menu {
    border: 1px solid rgba(251, 191, 36, 0.25);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }
  :host([dark]) .selora-menu {
    background: rgba(20, 20, 22, 0.92);
    box-shadow:
      0 12px 32px rgba(0, 0, 0, 0.55),
      0 0 24px rgba(251, 191, 36, 0.08);
  }
  :host(:not([dark])) .selora-menu {
    border-color: rgba(0, 0, 0, 0.1);
  }
  .overflow-section {
    display: flex;
    flex-direction: column;
  }
  /* Mobile-only nav section inside the menu (Automations / Scenes) */
  .overflow-section.narrow-only {
    display: none;
  }
  :host([narrow]) .overflow-section.narrow-only {
    display: flex;
  }
  .overflow-item {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    padding: 0 14px;
    height: 44px;
    background: none;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: var(--ha-font-size-m, 14px);
    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color: var(--primary-text-color);
    text-decoration: none;
    transition:
      background 0.15s,
      color 0.15s;
    box-sizing: border-box;
    --mdc-icon-size: 20px;
  }
  .overflow-item:hover {
    background: rgba(251, 191, 36, 0.08);
  }
  :host(:not([dark])) .overflow-item:hover {
    background: rgba(0, 0, 0, 0.05);
  }
  .overflow-item ha-icon {
    color: var(--secondary-text-color);
  }
  .overflow-item-label {
    flex: 1;
    text-align: left;
  }
  .overflow-item-external {
    --mdc-icon-size: 14px;
    opacity: 0.5;
  }
  .overflow-item.active {
    color: var(--selora-accent-text, #f59e0b);
    background: rgba(251, 191, 36, 0.1);
    font-weight: 600;
  }
  .overflow-item.active ha-icon {
    color: var(--selora-accent-text, #f59e0b);
  }
  :host(:not([dark])) .overflow-item.active {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.08);
  }
  :host(:not([dark])) .overflow-item.active ha-icon {
    color: var(--primary-text-color);
  }
  .overflow-divider {
    height: 1px;
    margin: 6px 4px;
    background: var(--divider-color, rgba(0, 0, 0, 0.12));
  }
  :host([dark]) .overflow-divider {
    background: rgba(251, 191, 36, 0.15);
  }
  /* card-tab underline used elsewhere — keep */
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
    position: relative;
    z-index: 1;
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

  /* ---- Welcome: composer-centered layout ---- */
  .chat-welcome-center {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow-y: auto;
    padding: 24px;
  }
  .welcome-center-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    max-width: 560px;
    width: 100%;
    animation: fadeInUp 0.5s ease both;
  }
  .welcome-center-content > img {
    animation: logoEntrance 0.7s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  }
  .welcome-center-content .chat-input {
    width: 100%;
  }
  .welcome-center-content .qa-group {
    width: 100%;
    justify-content: center;
  }

  /* Quick-start disclosure on the welcome screen */
  .welcome-quickstart {
    width: 100%;
    margin-top: 20px;
  }
  .welcome-quickstart-summary {
    list-style: none;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin: 0 auto 12px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 0.5;
    border-radius: 999px;
    transition:
      opacity 0.2s,
      background-color 0.2s;
    user-select: none;
  }
  .welcome-quickstart-summary::-webkit-details-marker {
    display: none;
  }
  .welcome-quickstart-summary:hover {
    opacity: 0.8;
    background-color: rgba(255, 255, 255, 0.04);
  }
  :host(:not([dark])) .welcome-quickstart-summary:hover {
    background-color: rgba(0, 0, 0, 0.04);
  }
  .welcome-quickstart-chevron {
    --mdc-icon-size: 16px;
    transition: transform 0.2s ease;
  }
  .welcome-quickstart[open] .welcome-quickstart-chevron {
    transform: rotate(180deg);
  }
  /* Center the summary itself in the centered welcome layout */
  .welcome-quickstart {
    display: flex;
    flex-direction: column;
    align-items: center;
  }

  /* Particle field surrounding the welcome composer */
  .welcome-composer-area {
    position: relative;
    width: 100%;
    padding: 56px 0;
    margin-top: 24px;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .welcome-composer-particles {
    position: absolute;
    top: 0;
    bottom: 0;
    left: -20%;
    right: -20%;
    pointer-events: none;
    opacity: 0;
    transition: opacity 1.2s ease;
    mask-image: radial-gradient(
      ellipse 70% 80% at center,
      black 25%,
      rgba(0, 0, 0, 0.6) 55%,
      transparent 85%
    );
    -webkit-mask-image: radial-gradient(
      ellipse 70% 80% at center,
      black 25%,
      rgba(0, 0, 0, 0.6) 55%,
      transparent 85%
    );
  }
  .welcome-composer-particles.visible {
    opacity: 1;
  }

  /* Particles above docked composer in ongoing chat */
  .chat-input-wrapper {
    position: relative;
    flex-shrink: 0;
    padding-bottom: env(safe-area-inset-bottom, 0px);
    background: var(--primary-background-color);
  }
  .composer-dock-particles {
    position: absolute;
    top: -20px;
    left: 0;
    right: 0;
    height: 20px;
    z-index: 0;
    opacity: 0;
    transition: opacity 1s ease;
    mask-image: linear-gradient(to top, black, transparent);
    -webkit-mask-image: linear-gradient(to top, black, transparent);
  }
  .composer-dock-particles.visible {
    opacity: 1;
  }

  @media (max-width: 600px) {
    .chat-welcome-center {
      padding: 16px 12px;
    }
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
    color: var(--primary-text-color);
    font-weight: 700;
  }
  /* Entity-list grid: hosts real HA hui-tile-card elements. Cards
     bring their own borders, padding, theming, click target — the
     grid only handles layout. Single-entity references render here
     too as a one-card grid; uniform look across all mentions. The
     minmax(240px, 1fr) sizing matches the default cell width HA uses
     on tile-style dashboard sections, so chat tiles don't truncate
     the friendly name (180px was too tight for long room names). */
  .selora-entity-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 8px;
    margin: 12px 0;
    width: 100%;
  }
  .selora-entity-grid > * {
    /* Cards default to 56px tall in tile mode; let them size themselves
       without our own min-height fighting it. */
    min-width: 0;
    /* Lift the card off the chat bubble. Layered shadow (tight inner
       contact + softer ambient drop) reads as physical depth so the
       embedded widget visibly pops rather than sitting flush. The top
       border-color highlight reinforces the upper edge to sell the
       lifted look in dark mode. */
    --ha-card-border-color: var(--selora-zinc-700);
    --ha-card-box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.3), 0 6px 16px rgba(0, 0, 0, 0.35);
  }
  :host(:not([dark])) .selora-entity-grid > * {
    --ha-card-border-color: var(--divider-color);
    --ha-card-box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.06), 0 4px 12px rgba(0, 0, 0, 0.1);
  }
  /* Suppress the stuck hover/focus tint that hui-entities-card paints
     on a row after the user taps it (the more-info dialog closes but
     the row keeps :focus-visible, leaving one card darker than the
     rest). Chat doesn't need a row-level affordance — the toggle/
     control inside the row is the click target. */
  .selora-entity-grid > *::part(content),
  .selora-entity-grid > * div.entity {
    background: transparent !important;
  }
  /* Area sub-headers in multi-area entity grids. The grid-column rule
     spans the header across the full row so the next row of tiles
     starts cleanly under it. Layout matches HA dashboard section
     headers: small uppercase label with the area icon to its left. */
  .selora-area-header {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    line-height: 1;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-top: 8px;
    margin-bottom: -2px;
  }
  .selora-area-header:first-child {
    margin-top: 0;
  }
  .selora-area-icon {
    /* Match the cap-height of the 12px uppercase label so the icon
       sits flush with the text, not floating above it. ha-icon needs
       both the CSS variable AND an explicit box size — the variable
       controls the SVG glyph, the box prevents the host element from
       reserving its 24px default and pushing the label down. */
    --mdc-icon-size: 14px;
    width: 14px;
    height: 14px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  /* ---- Stream interruption notice ---- */
  .stream-interrupt {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 12px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(244, 67, 54, 0.08);
    border: 1px solid rgba(244, 67, 54, 0.25);
    color: var(--error-color, #f44336);
    font-size: 13px;
  }
  .stream-interrupt-text {
    flex: 1;
    color: var(--primary-text-color);
  }
  /* Retry link in the bubble-meta — matches "Selora AI · time" rhythm
     but uses the accent colour so the failure state is visible without
     resorting to a button-style chip. */
  .stream-interrupt-retry {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 0;
    border: none;
    background: none;
    color: var(--selora-accent, #fbbf24);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    opacity: 0.85;
    transition: opacity 120ms ease;
  }
  .stream-interrupt-retry:hover,
  .stream-interrupt-retry:focus-visible {
    opacity: 1;
    text-decoration: underline;
    outline: none;
  }
  .stream-interrupt-retry ha-icon {
    color: var(--selora-accent, #fbbf24);
  }

  /* ---- Chat input ---- */
  .chat-input {
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
    background: transparent;
    display: block;
    padding: 0;
  }

  .composer-styled {
    position: relative;
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 56px;
    border: 1px solid rgba(251, 191, 36, 0.5);
    border-radius: 18px;
    padding: 10px 10px 10px 24px;
    background: var(--card-background-color, #27272a);
    box-sizing: border-box;
    overflow: hidden;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.18),
      inset 0 1px 0 rgba(255, 255, 255, 0.03);
    transition:
      border-color 0.25s ease,
      box-shadow 0.25s ease;
  }
  .composer-styled:focus-within {
    border-color: rgba(251, 191, 36, 0.55);
    box-shadow:
      0 0 0 1px rgba(251, 191, 36, 0.14),
      0 10px 30px rgba(0, 0, 0, 0.18),
      inset 0 1px 0 rgba(255, 255, 255, 0.04);
  }
  /* Welcome variant: contained input with top and bottom glow lines. */
  .composer-welcome {
    position: relative;
    z-index: 1;
    width: 100%;
    max-width: 640px;
  }
  /* Top edge: 1px gradient line, brightest in the middle */
  .composer-welcome::before {
    content: "";
    position: absolute;
    top: 0;
    left: 24px;
    right: 24px;
    height: 1px;
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(245, 158, 11, 0.85) 50%,
      transparent 100%
    );
    pointer-events: none;
    z-index: 0;
  }
  /* Bottom edge: matching 1px gradient line */
  .composer-welcome::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 24px;
    right: 24px;
    height: 1px;
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(245, 158, 11, 0.85) 50%,
      transparent 100%
    );
    pointer-events: none;
    z-index: 0;
  }
  /* Soft halos sitting above and below the composer, centered on the glow line */
  .welcome-composer-area::before,
  .welcome-composer-area::after {
    content: "";
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    width: 70%;
    max-width: 520px;
    height: 32px;
    background: radial-gradient(
      ellipse 50% 100% at center,
      rgba(251, 191, 36, 0.55) 0%,
      rgba(245, 158, 11, 0.22) 35%,
      rgba(245, 158, 11, 0.06) 65%,
      transparent 100%
    );
    filter: blur(5px);
    pointer-events: none;
    z-index: 0;
  }
  .welcome-composer-area::before {
    top: calc(50% - 27px - 16px);
  }
  .welcome-composer-area::after {
    top: calc(50% + 27px - 16px);
  }
  .composer-welcome:focus-within {
    border-color: rgba(251, 191, 36, 0.55);
  }
  .composer-welcome:focus-within::before,
  .composer-welcome:focus-within::after {
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(251, 191, 36, 1) 50%,
      transparent 100%
    );
  }
  .welcome-center-content .composer-styled {
    margin: 0;
  }
  .chat-input-wrapper .composer-styled {
    margin: 10px auto;
    max-width: calc(1200px - 48px);
    width: calc(100% - 48px);
  }
  @media (max-width: 600px) {
    .chat-input-wrapper .composer-styled {
      margin: 8px auto;
      width: calc(100% - 24px);
    }
  }

  .composer-textarea {
    position: relative;
    z-index: 1;
    flex: 1 1 auto;
    min-width: 0;
    width: auto;
    min-height: 36px;
    resize: none;
    border: none;
    outline: none;
    background: transparent;
    color: var(--primary-text-color);
    font-family: inherit;
    font-size: 15px;
    line-height: 22px;
    padding: 7px 0;
    margin: 0;
    max-height: 200px;
    overflow-y: auto;
    box-sizing: border-box;
    display: block;
    vertical-align: middle;
  }
  .composer-textarea::placeholder {
    color: var(--secondary-text-color);
    opacity: 0.7;
  }
  .composer-textarea:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .composer-send {
    position: relative;
    z-index: 1;
    flex: 0 0 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #fcd34d 0%, #fbbf24 50%, #f59e0b 100%);
    border: none;
    cursor: pointer;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    color: #1a1300;
    --mdc-icon-size: 18px;
    margin: 0;
    padding: 0;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.35),
      0 0 12px -2px rgba(251, 191, 36, 0.55),
      inset 0 1px 0 rgba(255, 255, 255, 0.35);
    transition:
      transform 0.15s ease,
      box-shadow 0.2s ease,
      opacity 0.15s ease;
  }
  .composer-send ha-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    line-height: 0;
  }
  .composer-send:hover {
    transform: scale(1.06);
    box-shadow:
      0 2px 4px rgba(0, 0, 0, 0.4),
      0 0 18px -2px rgba(251, 191, 36, 0.7),
      inset 0 1px 0 rgba(255, 255, 255, 0.4);
  }
  .composer-send:active {
    transform: scale(0.96);
  }
  .composer-send:disabled {
    cursor: default;
    transform: none;
    opacity: 0.7;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.25),
      inset 0 1px 0 rgba(255, 255, 255, 0.2);
  }
  .composer-send:disabled:hover {
    transform: none;
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
    background: transparent;
    border-bottom: 1px solid var(--divider-color);
    padding: 10px 14px;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: normal;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--primary-text-color);
  }
  .proposal-body {
    padding: 14px;
  }
  .proposal-body .flow-chart {
    align-items: center;
  }
  .proposal-body .flow-section {
    text-align: center;
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
    color: var(--secondary-text-color);
    display: flex;
    align-items: center;
    gap: 4px;
    margin-bottom: 8px;
    user-select: none;
  }
  .yaml-toggle:hover {
    color: var(--primary-text-color);
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

  /* ---- Scene entity list ---- */
  .scene-entity-list {
    margin: 8px 0 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    overflow: hidden;
  }
  .scene-entity-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    font-size: 12px;
    border-bottom: 1px solid var(--divider-color);
  }
  .scene-entity-row:last-child {
    border-bottom: none;
  }
  .scene-entity-name {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--primary-text-color);
    min-width: 0;
    overflow: hidden;
  }
  .scene-entity-name > span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .scene-entity-state {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    flex-shrink: 0;
  }
  .scene-entity-attr {
    font-size: 11px;
    opacity: 0.6;
    font-weight: 400;
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
  }
  .auto-row:first-child .auto-row-main {
    border-radius: 12px 12px 0 0;
  }
  .auto-row:last-child .auto-row-main {
    border-radius: 0 0 12px 12px;
  }
  .auto-row:only-child .auto-row-main {
    border-radius: 12px;
  }
  .auto-row {
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .auto-row:last-child {
    border-bottom: none;
  }
  .auto-row.disabled
    > .auto-row-main
    > :not(.burger-menu-wrapper):not(.auto-row-name):not(ha-icon) {
    opacity: 0.5;
  }
  .auto-row.disabled .auto-row-desc,
  .auto-row.disabled .auto-row-mobile-meta {
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
  .auto-row-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .needs-attention-pill {
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
    border-radius: 12px;
    background: #d32f2f;
    color: #fff;
    white-space: nowrap;
    flex-shrink: 0;
    cursor: pointer;
  }
  .needs-attention-pill:hover {
    background: #c62828;
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
    /* Hide scene rows' duplicate desc — the same text already renders in
       .auto-row-mobile-meta below. Automations keep their actual
       description visible (different content from the mobile meta). */
    .auto-row-desc--meta-only {
      display: none;
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
    /* Mobile layout:
       Row 1 — filter input (full width)
       Row 2 — status pills (full width)
       Row 3 — sort select + "+ New X" share a row
       Row 4 — bulk-edit / actions (own row) */
    .filter-row .filter-input-wrap {
      flex: 1 1 100% !important;
    }
    .filter-row .status-pills {
      flex: 1 1 100%;
      justify-content: stretch;
    }
    .filter-row .status-pills .status-pill {
      flex: 1;
    }
    .filter-row .sort-select {
      flex: 1 1 0;
      min-width: 0;
    }
    /* The trailing wrapper around "+ New X" — keep it on the same row
       as the sort select, no longer pushed to its own line. */
    .filter-row > div[style*="margin-left:auto"] {
      flex: 0 1 auto;
      margin-left: 0 !important;
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
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    line-height: 1.2;
    padding: 9px 34px 9px 14px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background-color: rgba(255, 255, 255, 0.06);
    color: var(--primary-text-color);
    cursor: pointer;
    transition:
      border-color 0.2s,
      background-color 0.2s;
    /* Hide the native chevron and draw our own so the select looks like
       the rest of the UI in both light and dark modes. */
    appearance: none;
    -webkit-appearance: none;
    -moz-appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%23a1a1aa' d='M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z'/></svg>");
    background-repeat: no-repeat;
    background-position: right 10px center;
    background-size: 16px 16px;
  }
  .sort-select:hover {
    border-color: rgba(255, 255, 255, 0.18);
    background-color: rgba(255, 255, 255, 0.1);
  }
  .sort-select:focus {
    outline: none;
    border-color: rgba(251, 191, 36, 0.45);
  }
  :host(:not([dark])) .sort-select {
    border-color: rgba(0, 0, 0, 0.1);
    background-color: rgba(0, 0, 0, 0.05);
  }
  :host(:not([dark])) .sort-select:hover {
    border-color: rgba(0, 0, 0, 0.15);
    background-color: rgba(0, 0, 0, 0.08);
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

  /* ---- Suggestions/automations grid ----
     Auto-fill with a minimum card width so the grid drops columns based on
     its actual rendered width, not window.innerWidth. This avoids the
     case where HA's sidebar is open and the panel container is narrow
     while the window is wide — we'd render 3 columns and the action
     buttons inside cards would overflow. 280px is the minimum width that
     keeps the Accept/Dismiss button row from spilling. */
  .automations-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    align-items: start;
    gap: 20px;
    margin-bottom: 16px;
  }
  .automations-grid .masonry-col {
    display: contents;
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
    /* Wrap up to 2 lines on desktop (avoid layout breakage in the masonry
       grid). Native title attribute / DOM tooltip reveals the full text. */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  /* On touch devices the suggestion cards stack full-width, so let the
     title wrap freely instead of clamping. */
  @media (hover: none) {
    .automations-grid .card h3 {
      -webkit-line-clamp: unset;
      line-clamp: unset;
      display: block;
      overflow: visible;
    }
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
  .settings-doc-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 18px;
    border-radius: 12px;
    background: rgba(251, 191, 36, 0.06);
    border: 1px solid rgba(251, 191, 36, 0.15);
    color: var(--primary-text-color);
    text-decoration: none;
    transition:
      background 0.15s,
      border-color 0.15s;
  }
  .settings-doc-banner:hover {
    background: rgba(251, 191, 36, 0.1);
    border-color: rgba(251, 191, 36, 0.3);
  }
  .settings-doc-banner strong {
    font-size: 13px;
    font-weight: 600;
  }
  .settings-doc-banner span {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-top: 2px;
  }
  .section-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin: 4px 0 0;
    font-weight: 400;
  }
  .settings-section-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.05em;
    color: var(--secondary-text-color);
    margin: 20px 0 12px;
  }
  .service-label-group {
    flex: 1;
    min-width: 0;
  }
  .service-label-group label {
    font-size: 14px;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .service-desc {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-top: 1px;
  }
  .settings-connect-block {
    padding-bottom: 12px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--selora-zinc-700);
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
    display: inline-flex;
    align-items: center;
    margin-top: 4px;
  }
  .key-hint.key-set {
    border-color: color-mix(
      in srgb,
      var(--success-color, #22c55e) 40%,
      transparent
    );
    background: color-mix(
      in srgb,
      var(--success-color, #22c55e) 6%,
      var(--selora-zinc-900)
    );
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
  /* Fixed-width right column for consistent alignment */
  .service-row ha-switch,
  .service-row > ha-icon-button,
  .mcp-token-row > ha-icon-button {
    flex-shrink: 0;
    width: 48px;
    display: flex;
    justify-content: center;
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
  .advanced-section[open] {
    padding-bottom: 20px;
  }
  .advanced-toggle {
    display: flex;
    align-items: center;
    gap: 12px;
    cursor: pointer;
    font-size: 20px;
    font-weight: 700;
    line-height: 1.2;
    color: var(--primary-text-color);
    list-style: none;
    /* Horizontal padding mirrors .section-card (32px desktop, 12px
       mobile). The card itself stays padding:0 so the toggle's hover
       state can span full width; everything else inside the card
       indents to the same column as sibling section-card content. */
    padding: 22px 32px;
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
    --mdc-icon-size: 20px;
    transition: transform 0.2s;
    opacity: 0.5;
  }
  .advanced-section[open] > .advanced-toggle .advanced-chevron {
    transform: rotate(90deg);
  }
  .advanced-section[open] > .advanced-toggle {
    margin-bottom: 4px;
  }
  .advanced-section .service-row {
    border-bottom: none !important;
    padding: 8px 32px;
  }
  .advanced-section .service-details {
    padding: 0 32px;
  }
  .advanced-section .settings-section-title {
    padding: 0 32px;
  }
  .advanced-section .service-row:first-of-type {
    padding-top: 8px;
  }
  .advanced-section .service-group {
    padding-bottom: 12px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .advanced-section .service-group:last-of-type {
    border-bottom: none;
    padding-bottom: 0;
    margin-bottom: 0;
  }
  .advanced-section > .card-save-bar {
    margin: 16px 0 0;
    padding: 0 32px;
  }
  .advanced-section .settings-separator {
    margin: 8px 32px;
  }
  .advanced-section .service-row:last-of-type {
    padding-bottom: 16px;
  }
  /* Match .section-card's tighter mobile padding so all settings
     cards line their content up at the same horizontal offset. */
  @media (max-width: 600px) {
    .advanced-toggle {
      padding: 18px 12px;
    }
    .advanced-section .service-row {
      padding-left: 12px;
      padding-right: 12px;
    }
    .advanced-section .service-details,
    .advanced-section .settings-section-title,
    .advanced-section > .card-save-bar {
      padding-left: 12px;
      padding-right: 12px;
    }
    .advanced-section .settings-separator {
      margin-left: 12px;
      margin-right: 12px;
    }
  }
  .settings-form ha-switch {
    /* Legacy mwc-switch tokens (HA <= 2025 builds) */
    --switch-checked-color: var(--selora-accent);
    --switch-checked-button-color: var(--selora-accent);
    --switch-checked-track-color: var(--selora-accent-dark);
    --mdc-theme-secondary: var(--selora-accent);
    /* Material 3 tokens — newer HA builds rebuilt ha-switch on md-switch */
    --md-sys-color-primary: var(--selora-accent);
    --md-sys-color-on-primary: var(--selora-accent-dark);
    --md-sys-color-primary-container: var(--selora-accent);
    --md-sys-color-on-primary-container: var(--selora-accent-dark);
    --md-switch-selected-track-color: var(--selora-accent);
    --md-switch-selected-handle-color: var(--selora-accent-dark);
    --md-switch-selected-icon-color: var(--selora-accent);
    --md-switch-selected-hover-track-color: var(--selora-accent);
    --md-switch-selected-pressed-track-color: var(--selora-accent);
    --md-switch-selected-focus-track-color: var(--selora-accent);
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
  .card-save-bar {
    display: flex;
    justify-content: flex-end;
    margin-top: 16px;
  }
  .save-feedback {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    margin-top: 8px;
    padding: 8px 12px;
    border-radius: 8px;
  }
  .save-feedback--success {
    color: var(--success-color, #22c55e);
    background: color-mix(
      in srgb,
      var(--success-color, #22c55e) 8%,
      transparent
    );
  }
  .save-feedback--error {
    color: var(--error-color, #ef4444);
    background: color-mix(in srgb, var(--error-color, #ef4444) 8%, transparent);
  }
  .key-hint-btn {
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .key-hint-btn:hover {
    border-color: var(--selora-accent);
    background: color-mix(
      in srgb,
      var(--success-color, #22c55e) 10%,
      var(--selora-zinc-900)
    );
  }
  .key-hint-action {
    --mdc-icon-size: 13px;
    opacity: 0.45;
    margin-left: 8px;
    color: var(--secondary-text-color);
    transition: opacity 0.15s;
  }
  .key-hint-btn:hover .key-hint-action {
    opacity: 0.8;
  }

  /* ── MCP Token Management ───────────────────────────── */

  .mcp-token-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 0 0 8px;
  }
  .mcp-token-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .mcp-token-info {
    flex: 1;
    min-width: 0;
  }
  .mcp-token-name {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .mcp-token-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 2px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .mcp-token-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    text-transform: capitalize;
  }
  .mcp-token-badge--read_only {
    background: rgba(59, 130, 246, 0.15);
    color: #60a5fa;
  }
  .mcp-token-badge--admin {
    background: rgba(251, 191, 36, 0.15);
    color: var(--selora-accent);
  }
  .mcp-token-badge--custom {
    background: rgba(168, 85, 247, 0.15);
    color: #c084fc;
  }

  /* Tool checklist in create dialog */
  .mcp-tool-checklist {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    max-height: 240px;
    overflow-y: auto;
    padding: 8px;
    background: var(--selora-zinc-900);
    border: 1px solid var(--selora-zinc-700);
    border-radius: 8px;
  }
  .mcp-tool-check {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--primary-text-color);
    padding: 4px 6px;
    border-radius: 4px;
    cursor: pointer;
  }
  .mcp-tool-check:hover {
    background: var(--selora-zinc-800);
  }
  .mcp-tool-check input[type="checkbox"] {
    accent-color: var(--selora-accent);
  }
`;

// src/panel/styles/usage.css.js
var usageStyles = i`
  .usage-view {
    max-width: 720px;
    margin: 0 auto;
    padding: 16px 0 24px;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  /* Breadcrumb-style back link sits above the title, muted, no chrome.
     Click target keeps a comfortable 36px height for touch. */
  .usage-crumb {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 6px 0;
    margin: 0 0 -4px;
    font-size: 13px;
    color: var(--secondary-text-color);
    text-decoration: none;
    align-self: flex-start;
    transition: color 0.15s;
  }
  .usage-crumb:hover {
    color: var(--primary-text-color);
  }
  .usage-crumb ha-icon {
    --mdc-icon-size: 18px;
    margin-left: -4px;
  }

  .usage-title-row {
    display: flex;
    align-items: baseline;
    gap: 14px;
    flex-wrap: wrap;
  }
  .usage-title-row h2 {
    font-size: 24px;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.01em;
  }
  .usage-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }

  .usage-tile-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-top: 4px;
  }
  @media (max-width: 600px) {
    .usage-tile-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }
  .usage-tile {
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid var(--divider-color);
    background: var(--primary-background-color);
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .usage-tile-head {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--secondary-text-color);
    font-size: 12px;
    font-weight: 500;
    min-width: 0;
  }
  .usage-tile-label {
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    letter-spacing: 0.04em;
  }
  .usage-tile-value {
    font-size: 24px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: var(--primary-text-color);
  }
  .usage-tile-sub {
    font-size: 11px;
    color: var(--secondary-text-color);
  }

  .usage-period-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-period-row:last-of-type {
    border-bottom: none;
  }
  .usage-period-title {
    font-size: 13px;
    color: var(--secondary-text-color);
    width: 110px;
    flex-shrink: 0;
  }
  .usage-period-cost {
    font-size: 16px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .usage-period-tokens {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-left: auto;
  }
  .usage-period-empty,
  .usage-period-loading {
    font-size: 13px;
    color: var(--secondary-text-color);
    font-style: italic;
  }
  .usage-period-note {
    margin-top: 12px;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
    font-size: 12px;
    color: var(--secondary-text-color);
    line-height: 1.5;
  }

  .usage-help {
    font-size: 13px;
    color: var(--secondary-text-color);
    line-height: 1.55;
    margin: 0 0 8px;
  }
  .usage-help:last-child {
    margin-bottom: 0;
  }
  .usage-help a {
    color: var(--primary-color);
    text-decoration: none;
  }
  .usage-help a:hover {
    text-decoration: underline;
  }
  .usage-help code {
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 12px;
  }

  .usage-empty {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }
  .usage-empty p {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--secondary-text-color);
    line-height: 1.5;
  }

  .usage-section-sub {
    font-size: 11px;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    line-height: 1.2;
  }
  /* Heading next to a small caption: align on the text baseline so the
     two label rows read as one. The default 'align-items: center' for
     section headers centers line boxes, which leaves big+small text
     visually misaligned. */
  .section-card-header:has(.usage-section-sub) {
    align-items: baseline;
  }

  .usage-breakdown {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .usage-breakdown-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .usage-breakdown-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    font-variant-numeric: tabular-nums;
  }
  .usage-breakdown-label {
    font-size: 14px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .usage-breakdown-cost {
    font-size: 14px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .usage-breakdown-bar {
    height: 6px;
    border-radius: 999px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    overflow: hidden;
  }
  .usage-breakdown-bar-fill {
    height: 100%;
    background: linear-gradient(
      90deg,
      rgba(251, 191, 36, 0.85),
      rgba(184, 134, 11, 0.85)
    );
    border-radius: 999px;
    transition: width 0.4s ease;
  }
  .usage-breakdown-meta {
    display: flex;
    gap: 8px;
    font-size: 12px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-breakdown-intents {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 2px;
  }
  .usage-intent-pill {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }

  .usage-recent-list {
    display: flex;
    flex-direction: column;
  }
  .usage-recent-row {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider-color);
  }
  .usage-recent-row:last-child {
    border-bottom: none;
  }
  .usage-recent-main {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
  }
  .usage-recent-kind {
    font-weight: 600;
    color: var(--primary-text-color);
    font-size: 13px;
  }
  .usage-recent-intent {
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .usage-recent-time {
    margin-left: auto;
    font-size: 11px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-recent-details {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 12px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-recent-model {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .usage-recent-tokens {
    text-align: right;
    min-width: 64px;
  }
  .usage-recent-cost {
    color: var(--primary-text-color);
    font-weight: 500;
    text-align: right;
    min-width: 60px;
  }

  /* Right-aligned action link inside a section-card-header. Used for
     drill-downs from Settings → sub-page (e.g. token usage). Designed to
     sit alongside the header title without competing with primary CTAs. */
  .section-card-header--with-action {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .section-card-action {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    margin-right: -8px;
    border: none;
    background: transparent;
    color: var(--secondary-text-color);
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    border-radius: 6px;
    transition:
      color 0.15s,
      background 0.15s;
  }
  .section-card-action:hover {
    color: var(--primary-text-color);
    background: var(--secondary-background-color, rgba(255, 255, 255, 0.04));
  }
  .section-card-action ha-icon {
    --mdc-icon-size: 16px;
  }
  .section-card-action-chevron {
    --mdc-icon-size: 16px !important;
    opacity: 0.6;
    margin-left: -2px;
  }

  /* Pricing override: side-by-side input/output cells with a default hint
     line beneath each value. Edit row drops to a column on narrow screens
     so the textfields stay legible. */
  .usage-pricing-row {
    display: flex;
    gap: 12px;
    margin: 8px 0 12px;
    flex-wrap: wrap;
  }
  .usage-pricing-cell {
    flex: 1;
    min-width: 140px;
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .usage-pricing-label {
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
  }
  .usage-pricing-value {
    font-size: 18px;
    font-weight: 600;
    color: var(--primary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-pricing-default {
    font-size: 11px;
    color: var(--secondary-text-color);
  }
  .usage-pricing-edit {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-end;
    margin: 4px 0 12px;
  }
  .usage-pricing-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .usage-sensor-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 8px 0 4px;
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.03));
  }
  .usage-sensor-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 12px;
    min-width: 0;
  }
  .usage-sensor-row code {
    font-size: 11px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    padding: 2px 6px;
    border-radius: 4px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .usage-sensor-name {
    color: var(--secondary-text-color);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .usage-yaml-block {
    margin: 8px 0;
    padding: 12px 14px;
    border-radius: 10px;
    background: var(--primary-background-color);
    border: 1px solid var(--divider-color);
    overflow-x: auto;
    position: relative;
  }
  .usage-yaml-block code {
    font-size: 12px;
    line-height: 1.6;
    color: var(--primary-text-color);
    white-space: pre;
    background: none;
    padding: 0;
  }
  .usage-yaml-block .yaml-line {
    white-space: pre;
  }
  .usage-yaml-block .yaml-key {
    color: var(--error-color, #c62828);
  }
  .usage-yaml-block .yaml-colon {
    color: var(--secondary-text-color);
  }
  .usage-yaml-block .yaml-val {
    color: var(--success-color, #2e7d32);
  }
  .usage-yaml-block .yaml-dash {
    color: var(--secondary-text-color);
  }

  .usage-snippet-pills {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin: 8px 0 4px;
  }
  .usage-snippet-pill {
    font-size: 12px;
    font-weight: 500;
    padding: 5px 12px;
    border-radius: 999px;
    border: 1px solid var(--divider-color);
    background: transparent;
    color: var(--secondary-text-color);
    cursor: pointer;
    transition:
      color 0.15s,
      background 0.15s,
      border-color 0.15s;
  }
  .usage-snippet-pill:hover {
    color: var(--primary-text-color);
    border-color: var(--primary-text-color);
  }
  .usage-snippet-pill.active {
    background: rgba(184, 134, 11, 0.85);
    border-color: rgba(184, 134, 11, 0.85);
    color: #fff;
  }

  .usage-copy-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    font-size: 11px;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--secondary-text-color);
    cursor: pointer;
    transition:
      color 0.15s,
      background 0.15s;
  }
  .usage-copy-btn:hover {
    color: var(--primary-text-color);
    background: var(--secondary-background-color, rgba(255, 255, 255, 0.06));
  }
`;

// src/panel/styles/quick-actions.css.js
var quickActionStyles = i`
  /* ── Shared quick-action container ── */
  .qa-group {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }

  /* ────────────────────────────────────────────────────────────────────
   * Animated comet that travels along the chip's border perimeter.
   *
   * Technique (port of the React/Tailwind reference):
   *   - .qa-glow-track is a transparent-bordered overlay sitting at -1px on
   *     the chip. A two-layer mask shows ONLY the border ring of the chip
   *     (mask-composite: intersect with linear-gradient(transparent) on
   *     padding-box and linear-gradient(#000) on border-box).
   *   - .qa-glow-spot inside it follows offset-path: rect(0 auto auto 0
   *     round Npx) — the rectangular perimeter — animated via offset-distance
   *     0% → 100%, 5s linear infinite. The spot has a horizontal gradient
   *     fading from transparent to brand color, giving a comet trail look.
   *
   * Brand color: --selora-accent (gold) in dark, HA --primary-color in light.
   * ──────────────────────────────────────────────────────────────────── */

  @keyframes qa-spot-travel {
    to {
      offset-distance: 100%;
    }
  }

  .qa-suggestion,
  .qa-choice {
    --qa-spot-color: var(--selora-accent, #fbbf24);
    /* Match the header tab fill/border (translucent ghost pill) */
    --qa-bg: rgba(255, 255, 255, 0.06);
    --qa-border-color: rgba(255, 255, 255, 0.08);
    --qa-bg-hover: rgba(255, 255, 255, 0.1);
    --qa-border-hover: rgba(255, 255, 255, 0.12);
    --qa-radius: 999px;
    --qa-spot-size: 24px;
    --qa-spot-duration: 5s;

    position: relative;
    isolation: isolate;
    cursor: pointer;
    color: var(--primary-text-color);
    border: 1px solid var(--qa-border-color);
    border-radius: var(--qa-radius);
    background: var(--qa-bg);
  }

  /* Light mode: match light-mode tabs */
  :host(:not([dark])) .qa-suggestion,
  :host(:not([dark])) .qa-choice {
    --qa-spot-color: var(--primary-color, #03a9f4);
    --qa-bg: rgba(0, 0, 0, 0.05);
    --qa-border-color: rgba(0, 0, 0, 0.1);
    --qa-bg-hover: rgba(0, 0, 0, 0.08);
    --qa-border-hover: rgba(0, 0, 0, 0.15);
  }

  /* Comet track — masked so its contents only paint on the border ring */
  .qa-glow-track {
    position: absolute;
    inset: -1px;
    border-radius: inherit;
    border: 2px solid transparent;
    pointer-events: none;
    z-index: 0;
    -webkit-mask:
      linear-gradient(transparent, transparent), linear-gradient(#000, #000);
    -webkit-mask-clip: padding-box, border-box;
    -webkit-mask-composite: source-in;
    mask:
      linear-gradient(transparent, transparent), linear-gradient(#000, #000);
    mask-clip: padding-box, border-box;
    mask-composite: intersect;
  }

  /* The traveling spot. offset-path traces the perimeter; the comet trail
     is a horizontal gradient (transparent → spot color) sized to the spot. */
  .qa-glow-spot {
    position: absolute;
    width: var(--qa-spot-size);
    height: var(--qa-spot-size);
    background: linear-gradient(
      to right,
      transparent 0%,
      var(--qa-spot-color) 100%
    );
    offset-path: rect(0 auto auto 0 round var(--qa-radius));
    offset-distance: 0%;
    animation: qa-spot-travel var(--qa-spot-duration) linear infinite;
  }

  @media (prefers-reduced-motion: reduce) {
    .qa-glow-spot {
      animation: none;
    }
  }

  /* On touch devices, chips stack vertically and have full row width, so
     drop label truncation entirely and let titles wrap to as many lines
     as needed. Desktop keeps the ellipsis/line-clamp behavior. */
  @media (hover: none) {
    .qa-suggestion-label {
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
    }
    .qa-choice-label {
      display: block;
      -webkit-line-clamp: unset;
      line-clamp: unset;
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
    }
  }

  /* ── Suggestion chips (welcome / quick-start, scene suggestions) ── */
  .qa-suggestion {
    --qa-radius: 12px;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px 12px 16px;
    min-width: 0;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
    line-height: 1.3;
    transition:
      background-color 0.15s,
      border-color 0.15s,
      transform 0.15s,
      box-shadow 0.2s;
  }
  .qa-suggestion:hover {
    border-color: var(--qa-border-hover);
    background-color: var(--qa-bg-hover);
  }
  .qa-suggestion:hover .qa-suggestion-trail {
    color: var(--qa-spot-color);
    transform: translateX(2px);
  }
  .qa-suggestion:active {
    transform: translateY(0) scale(0.99);
  }
  .qa-suggestion ha-icon {
    --mdc-icon-size: 18px;
    flex-shrink: 0;
    position: relative;
    z-index: 1;
  }
  .qa-suggestion-lead {
    color: var(--qa-spot-color);
    opacity: 0.85;
  }
  .qa-suggestion-label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    position: relative;
    z-index: 1;
  }
  .qa-suggestion-trail {
    --mdc-icon-size: 16px !important;
    color: var(--secondary-text-color);
    opacity: 0.5;
    transition:
      color 0.15s,
      transform 0.15s,
      opacity 0.15s;
  }

  /* ── Choice cards (AI-offered options) ── */
  .qa-group--choices {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 10px;
  }
  .qa-choice {
    --qa-radius: 12px;
    display: flex;
    padding: 12px 14px;
    text-align: left;
    transition:
      background-color 0.15s,
      border-color 0.15s,
      transform 0.15s,
      box-shadow 0.2s;
  }
  .qa-choice:hover {
    border-color: var(--qa-border-hover);
    background-color: var(--qa-bg-hover);
  }
  .qa-choice:hover .qa-choice-trail {
    color: var(--qa-spot-color);
    transform: translateX(2px);
  }
  .qa-choice:active {
    transform: translateY(0) scale(0.99);
  }
  .qa-choice > *:not(.qa-glow-track) {
    position: relative;
    z-index: 1;
  }
  .qa-choice-row {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    min-width: 0;
  }
  .qa-choice ha-icon {
    --mdc-icon-size: 18px;
    flex-shrink: 0;
  }
  .qa-choice-lead {
    color: var(--qa-spot-color);
    opacity: 0.85;
  }
  .qa-choice-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }
  .qa-choice-label {
    font-size: 13px;
    font-weight: 600;
    line-height: 1.3;
    /* Wrap up to 2 lines, then ellipsis (line-clamp) */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  .qa-choice-desc {
    font-size: 12px;
    opacity: 0.6;
    line-height: 1.35;
  }
  .qa-choice-trail {
    --mdc-icon-size: 16px !important;
    color: var(--secondary-text-color);
    opacity: 0.5;
    transition:
      color 0.15s,
      transform 0.15s,
      opacity 0.15s;
  }

  /* ── Confirmation buttons (Apply / Modify / Cancel) ── */
  .qa-group--confirmations {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .qa-confirm {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border-radius: 8px;
    transition:
      background 0.15s,
      border-color 0.15s;
    white-space: nowrap;
    border: 1px solid
      var(--selora-inner-card-border, var(--divider-color, #3f3f46));
    background: transparent;
    color: var(--primary-text-color);
  }
  .qa-confirm:hover {
    border-color: var(--selora-accent);
  }
  .qa-confirm--primary {
    background: var(--selora-accent);
    color: #000;
    border-color: var(--selora-accent);
  }
  .qa-confirm--primary:hover {
    background: #f59e0b;
    border-color: #f59e0b;
  }
  .qa-confirm ha-icon {
    --mdc-icon-size: 14px;
  }

  /* ── Disabled state (after selection) ── */
  .qa-group--used {
    opacity: 0.45;
    pointer-events: none;
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
  usageStyles,
  quickActionStyles,
];

// src/shared/particles.js
var TAU = Math.PI * 2;
var FRAME_INTERVAL = 1e3 / 60;
function rand(min, max) {
  return Math.random() * (max - min) + min;
}
function parseHexColor(hex) {
  const n5 = parseInt(hex.slice(1), 16);
  return [(n5 >> 16) & 255, (n5 >> 8) & 255, n5 & 255];
}
var SparkleEngine = class {
  constructor(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.dpr = window.devicePixelRatio || 1;
    this.particles = [];
    this.rafId = 0;
    this.lastFrame = 0;
    this.w = 0;
    this.h = 0;
    this.color = opts.color || "#C7AE6A";
    this.count = opts.count || 400;
    this.maxOpacity = opts.maxOpacity ?? 1;
    this._rgb = parseHexColor(this.color);
  }
  resize(width, height) {
    this.w = width;
    this.h = height;
    this.canvas.width = width * this.dpr;
    this.canvas.height = height * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }
  init() {
    this.particles = [];
    for (let i5 = 0; i5 < this.count; i5++) {
      const yBias = Math.random();
      this.particles.push({
        x: rand(0, this.w),
        y: yBias * yBias * this.h,
        vx: rand(-1, 1),
        vy: rand(-1, 1),
        size: rand(0.4, 1.4),
        opacity: rand(0.1, this.maxOpacity),
        opacitySpeed: rand(8e-3, 0.03),
        opacityDir: Math.random() > 0.5 ? 1 : -1,
      });
    }
  }
  renderStatic() {
    this.init();
    this._draw();
  }
  start() {
    this.init();
    this.lastFrame = 0;
    this._loop(0);
  }
  _loop = (ts) => {
    this.rafId = requestAnimationFrame(this._loop);
    if (ts - this.lastFrame < FRAME_INTERVAL) return;
    this.lastFrame = ts;
    this._update();
    this._draw();
  };
  _update() {
    const { w: w2, h: h3, maxOpacity, particles } = this;
    for (let i5 = 0, len = particles.length; i5 < len; i5++) {
      const p2 = particles[i5];
      p2.x += p2.vx;
      p2.y += p2.vy;
      if (p2.x < 0) p2.x = w2;
      else if (p2.x > w2) p2.x = 0;
      if (p2.y < 0) {
        p2.y = h3;
      } else if (p2.y > h3) {
        const r4 = Math.random();
        p2.y = r4 * r4 * h3 * 0.5;
      }
      p2.opacity += p2.opacitySpeed * p2.opacityDir;
      if (p2.opacity >= maxOpacity) {
        p2.opacity = maxOpacity;
        p2.opacityDir = -1;
      } else if (p2.opacity <= 0.1) {
        p2.opacity = 0.1;
        p2.opacityDir = 1;
      }
    }
  }
  _draw() {
    const { ctx, w: w2, h: h3, particles, _rgb } = this;
    const [r4, g2, b2] = _rgb;
    ctx.clearRect(0, 0, w2, h3);
    for (let i5 = 0, len = particles.length; i5 < len; i5++) {
      const p2 = particles[i5];
      ctx.globalAlpha = p2.opacity;
      ctx.fillStyle = `rgb(${r4},${g2},${b2})`;
      ctx.beginPath();
      ctx.arc(p2.x, p2.y, p2.size, 0, TAU);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  destroy() {
    cancelAnimationFrame(this.rafId);
    this.rafId = 0;
    this.particles = [];
    this.ctx = null;
    this.canvas = null;
  }
};
var SeloraParticles = class extends HTMLElement {
  // Property setters keep the running engine in sync when Lit updates
  // .color / .maxOpacity bindings (e.g. user toggles dark mode).
  set color(value) {
    this._color = value;
    if (this._engine && value) {
      this._engine.color = value;
      this._engine._rgb = parseHexColor(value);
    }
  }
  get color() {
    return this._color;
  }
  set maxOpacity(value) {
    this._maxOpacity = value;
    if (this._engine && value != null) {
      this._engine.maxOpacity = value;
    }
  }
  get maxOpacity() {
    return this._maxOpacity;
  }
  connectedCallback() {
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    canvas.setAttribute("role", "presentation");
    canvas.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none;touch-action:none;";
    this.appendChild(canvas);
    this._canvas = canvas;
    const count = this.count || 400;
    const color = this._color || "#C7AE6A";
    const maxOpacity = this._maxOpacity ?? 1;
    this._engine = new SparkleEngine(canvas, { color, count, maxOpacity });
    this._ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          this._engine?.resize(width, height);
          if (!this._started) {
            this._start();
          }
        }
      }
    });
    this._ro.observe(this);
  }
  _start() {
    if (!this._engine) return;
    this._started = true;
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reducedMotion) {
      this._engine.renderStatic();
    } else {
      this._engine.start();
    }
    this.classList.add("visible");
  }
  disconnectedCallback() {
    this._ro?.disconnect();
    this._ro = null;
    this._engine?.destroy();
    this._engine = null;
    this._started = false;
    if (this._canvas) {
      this._canvas.remove();
      this._canvas = null;
    }
  }
};
customElements.define("selora-particles", SeloraParticles);

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
    return {
      text: "",
      hasAutomationBlock: false,
      isPartialBlock: false,
      partialBlockType: null,
    };
  const blockTypes =
    "automation|scene|quick_actions|command|delayed_command|cancel";
  const completeRe = new RegExp("```(?:" + blockTypes + ")[\\s\\S]*?```", "g"); // nosemgrep
  const hasComplete = completeRe.test(text);
  let cleaned = text.replace(completeRe, "").trim();
  const partialRe = new RegExp("```(" + blockTypes + ")[\\s\\S]*$"); // nosemgrep
  const partialMatch = !hasComplete ? cleaned.match(partialRe) : null;
  const hasPartial = !!partialMatch;
  if (hasPartial) {
    cleaned = cleaned.replace(partialRe, "").trim();
  }
  const spinnerType = ["automation", "scene"].includes(partialMatch?.[1])
    ? partialMatch[1]
    : null;
  return {
    text: cleaned,
    hasAutomationBlock: hasComplete,
    isPartialBlock: hasPartial,
    partialBlockType: spinnerType,
  };
}
function _coalesceEntityListings(text) {
  const ID_LINE = /^[\s>]*[-•*]?\s*([a-z_]+\.[a-z0-9_\-]+)\s*$/;
  const MARKER_BULLET =
    /^[\s>]*[-•*]\s*(\[\[entit(?:y|ies):[^\]\n]+\]\])\s*(?:[—–][^\n]*)?$/;
  const MARKER_TAIL_STATE =
    /^(\s*\[\[entit(?:y|ies):[^\]\n]+\]\])\s*[—–][^\n]*$/;
  const STATE_LINE = /^[\s>]*[-•*]?\s*[—–]\s*\S/;
  const BLANK = /^\s*$/;
  const lines = text.split("\n");
  const out = [];
  let i5 = 0;
  const skipBlanks = (j2) => {
    while (j2 < lines.length && BLANK.test(lines[j2])) j2++;
    return j2;
  };
  const skipStateLines = (j2) => {
    while (j2 < lines.length) {
      const k2 = skipBlanks(j2);
      if (k2 >= lines.length || !STATE_LINE.test(lines[k2])) return j2;
      j2 = k2 + 1;
    }
    return j2;
  };
  const idsFromMarker = (marker) => {
    const single = marker.match(/^\[\[entity:([a-z_]+\.[a-z0-9_\-]+)/);
    if (single) return [single[1]];
    const multi = marker.match(/^\[\[entities:([^\]\n]+)\]\]/);
    if (multi) {
      return multi[1]
        .split(",")
        .map((s6) => s6.trim())
        .filter((s6) => /^[a-z_]+\.[a-z0-9_\-]+$/.test(s6));
    }
    return [];
  };
  const BARE_MARKER =
    /^\s*(\[\[entit(?:y|ies):[^\]\n]+\]\])\s*(?:[—–][^\n]*)?$/;
  while (i5 < lines.length) {
    const tryCoalesce = (firstLineRe) => {
      if (!firstLineRe.test(lines[i5])) return false;
      const runIds = [];
      let j3 = i5;
      while (j3 < lines.length) {
        const m2 = lines[j3].match(firstLineRe);
        if (!m2) break;
        for (const id of idsFromMarker(m2[1])) runIds.push(id);
        j3++;
        j3 = skipStateLines(j3);
        j3 = skipBlanks(j3);
      }
      if (runIds.length === 0) return false;
      out.push(`[[entities:${runIds.join(",")}]]`);
      i5 = j3;
      return true;
    };
    if (tryCoalesce(MARKER_BULLET)) continue;
    if (tryCoalesce(BARE_MARKER)) continue;
    const tailMatch = lines[i5].match(MARKER_TAIL_STATE);
    if (tailMatch) {
      out.push(tailMatch[1]);
      let j3 = i5 + 1;
      j3 = skipStateLines(j3);
      i5 = j3;
      continue;
    }
    const ids = [];
    let j2 = i5;
    while (j2 < lines.length) {
      const m2 = lines[j2].match(ID_LINE);
      if (!m2) break;
      ids.push(m2[1]);
      j2++;
      j2 = skipStateLines(j2);
      j2 = skipBlanks(j2);
    }
    if (ids.length >= 1) {
      out.push(`[[entities:${ids.join(",")}]]`);
      i5 = j2;
      continue;
    }
    out.push(lines[i5]);
    i5++;
  }
  return out.join("\n");
}
function renderMarkdown(text) {
  if (!text) return "";
  text = text.replace(/\[\[entit(?:y|ies):[^\]\n]*$/, "");
  text = text
    .replace(/^\[([A-Za-z_ ]+)\]\s*\(\d+[^)]*\)\s*:?\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  text = _coalesceEntityListings(text);
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
    /\[\[entity:([a-z_]+\.[a-z0-9_\-]+)\|[^\]]+?\]\]/g,
    (_m, id) =>
      `<div class="selora-entity-grid" data-entity-ids="${id}"></div>`,
  );
  escaped = escaped.replace(
    /\[\[entities:([a-z_]+\.[a-z0-9_\-]+(?:,[a-z_]+\.[a-z0-9_\-]+)*)\]\]/g,
    (_m, ids) =>
      `<div class="selora-entity-grid" data-entity-ids="${ids}"></div>`,
  );
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
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
  escaped = escaped.replace(/(<br>)+(<div class="selora-entity-grid")/g, "$2");
  escaped = escaped.replace(/(selora-entity-grid[^"]*"><\/div>)(<br>)+/g, "$1");
  return escaped;
}

// src/panel/render-device-detail.js
function _formatTimestamp(iso) {
  if (!iso) return "";
  const d3 = new Date(iso);
  return d3.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function _stateColor(state) {
  if (!state) return "var(--selora-zinc-400)";
  const s6 = String(state).toLowerCase();
  if (["on", "open", "home", "playing", "active"].includes(s6))
    return "var(--selora-accent, #fbbf24)";
  if (["off", "closed", "not_home", "idle", "standby"].includes(s6))
    return "var(--selora-zinc-400)";
  if (["unavailable", "unknown"].includes(s6)) return "#ef4444";
  return "var(--selora-zinc-200)";
}
function _deviceIcon(domains) {
  if (!domains || !domains.length) return "mdi:devices";
  const d3 = domains[0];
  const map = {
    light: "mdi:lightbulb",
    switch: "mdi:toggle-switch",
    sensor: "mdi:eye",
    binary_sensor: "mdi:motion-sensor",
    climate: "mdi:thermostat",
    cover: "mdi:window-shutter",
    media_player: "mdi:speaker",
    camera: "mdi:cctv",
    lock: "mdi:lock",
    fan: "mdi:fan",
  };
  return map[d3] || "mdi:devices";
}
function renderDeviceDetail(host) {
  const detail = host._deviceDetail;
  if (!detail) return "";
  const loading = host._deviceDetailLoading;
  return x`
    <div
      class="device-detail-drawer"
      style="
      margin-top:12px;padding:14px;
      border:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));
      border-radius:12px;
      background:var(--selora-inner-card-bg, var(--primary-background-color, #18181b));
    "
    >
      ${
        loading
          ? x`<span style="font-size:13px;color:var(--selora-zinc-400);"
            >Loading device detail...</span
          >`
          : x`
            <!-- Header -->
            <div
              style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;"
            >
              <div style="display:flex;align-items:center;gap:8px;">
                <ha-icon
                  icon=${_deviceIcon(detail.entities?.map((e5) => e5.domain))}
                  style="--mdc-icon-size:22px;color:var(--selora-accent);"
                ></ha-icon>
                <div>
                  <div
                    style="font-weight:700;font-size:15px;color:var(--selora-zinc-200);"
                  >
                    ${detail.name}
                  </div>
                  <div style="font-size:12px;color:var(--selora-zinc-400);">
                    ${[detail.area, detail.manufacturer, detail.model].filter(Boolean).join(" \xB7 ")}
                    ${
                      detail.integration
                        ? x` ·
                          <span style="opacity:0.7"
                            >${detail.integration}</span
                          >`
                        : ""
                    }
                  </div>
                </div>
              </div>
              <button
                style="background:none;border:none;cursor:pointer;color:var(--selora-zinc-400);padding:4px;"
                @click=${() => {
                  host._deviceDetail = null;
                }}
                title="Close"
              >
                <ha-icon
                  icon="mdi:close"
                  style="--mdc-icon-size:18px;"
                ></ha-icon>
              </button>
            </div>

            <!-- Entities -->
            ${
              detail.entities?.length
                ? x`
                  <div style="margin-bottom:12px;">
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      Entities
                    </div>
                    ${detail.entities.map(
                      (e5) => x`
                        <div
                          style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));"
                        >
                          <span
                            style="font-size:12px;color:var(--selora-zinc-200);"
                            >${e5.name || e5.entity_id}</span
                          >
                          <span
                            style="font-size:12px;font-weight:600;color:${_stateColor(
                              e5.state,
                            )};"
                            >${e5.state}</span
                          >
                        </div>
                      `,
                    )}
                  </div>
                `
                : ""
            }

            <!-- State History -->
            ${
              detail.state_history?.length
                ? x`
                  <div style="margin-bottom:12px;">
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      State History (24h)
                    </div>
                    <div style="max-height:150px;overflow-y:auto;">
                      ${detail.state_history.slice(0, 30).map(
                        (h3) => x`
                          <div
                            style="display:flex;justify-content:space-between;padding:3px 0;font-size:11px;"
                          >
                            <span style="color:var(--selora-zinc-400);"
                              >${h3.entity_id.split(".")[1]}</span
                            >
                            <span style="color:${_stateColor(h3.state)};"
                              >${h3.state}</span
                            >
                            <span style="color:var(--selora-zinc-400);"
                              >${_formatTimestamp(h3.last_changed)}</span
                            >
                          </div>
                        `,
                      )}
                    </div>
                  </div>
                `
                : ""
            }

            <!-- Linked Automations -->
            ${
              detail.linked_automations?.length
                ? x`
                  <div style="margin-bottom:12px;">
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      Linked Automations
                    </div>
                    ${detail.linked_automations.map(
                      (a4) => x`
                        <div
                          style="padding:4px 0;border-bottom:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));"
                        >
                          <span
                            style="font-size:12px;color:var(--selora-zinc-200);"
                            >${a4.alias || a4.id}</span
                          >
                        </div>
                      `,
                    )}
                  </div>
                `
                : ""
            }

            <!-- Related Patterns -->
            ${
              detail.related_patterns?.length
                ? x`
                  <div>
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      Detected Patterns
                    </div>
                    ${detail.related_patterns.map(
                      (p2) => x`
                        <div
                          style="padding:4px 0;border-bottom:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));"
                        >
                          <div
                            style="font-size:12px;color:var(--selora-zinc-200);"
                          >
                            ${p2.description}
                          </div>
                          <div
                            style="font-size:10px;color:var(--selora-zinc-400);margin-top:2px;"
                          >
                            ${p2.type} · ${Math.round(p2.confidence * 100)}%
                            confidence
                          </div>
                        </div>
                      `,
                    )}
                  </div>
                `
                : ""
            }
          `
      }
    </div>
  `;
}

// src/panel/quick-actions.js
function renderQuickActions(host, actions, opts = {}) {
  if (!actions || !actions.length) return "";
  const mode = _detectMode(actions);
  const usedClass = opts.used ? " qa-group--used" : "";
  if (mode === "choice") {
    return x`
      <div class="qa-group qa-group--choices${usedClass}">
        ${actions.map((a4) => _renderChoice(host, a4))}
      </div>
    `;
  }
  if (mode === "confirmation") {
    return x`
      <div class="qa-group qa-group--confirmations${usedClass}">
        ${actions.map((a4) => _renderConfirmation(host, a4))}
      </div>
    `;
  }
  return x`
    <div class="qa-group${usedClass}">
      ${actions.map((a4) => _renderSuggestion(host, a4))}
    </div>
  `;
}
function _detectMode(actions) {
  const first = actions[0];
  if (first.mode) return first.mode;
  if (actions.some((a4) => a4.primary !== void 0)) return "confirmation";
  if (actions.some((a4) => a4.description)) return "choice";
  return "suggestion";
}
function _onSelect(host, action) {
  host._selectQuickAction(action);
}
function _renderSuggestion(host, action) {
  const leadingIcon = action.icon || "mdi:auto-fix";
  return x`
    <button class="qa-suggestion" @click=${() => _onSelect(host, action)}>
      <span class="qa-glow-track" aria-hidden="true">
        <span class="qa-glow-spot"></span>
      </span>
      <ha-icon class="qa-suggestion-lead" icon=${leadingIcon}></ha-icon>
      <span class="qa-suggestion-label">${action.label}</span>
      <ha-icon class="qa-suggestion-trail" icon="mdi:chevron-right"></ha-icon>
    </button>
  `;
}
function _renderChoice(host, action) {
  const leadingIcon = action.icon || "mdi:auto-fix";
  return x`
    <div class="qa-choice" @click=${() => _onSelect(host, action)}>
      <span class="qa-glow-track" aria-hidden="true">
        <span class="qa-glow-spot"></span>
      </span>
      <div class="qa-choice-row">
        <ha-icon class="qa-choice-lead" icon=${leadingIcon}></ha-icon>
        <div class="qa-choice-text">
          <span class="qa-choice-label" title=${action.label}
            >${action.label}</span
          >
          ${action.description ? x`<span class="qa-choice-desc">${action.description}</span>` : ""}
        </div>
        <ha-icon class="qa-choice-trail" icon="mdi:chevron-right"></ha-icon>
      </div>
    </div>
  `;
}
function _renderConfirmation(host, action) {
  const cls = action.primary ? "qa-confirm qa-confirm--primary" : "qa-confirm";
  return x`
    <button class=${cls} @click=${() => _onSelect(host, action)}>
      ${action.icon ? x`<ha-icon icon=${action.icon}></ha-icon>` : ""}
      ${action.label}
    </button>
  `;
}

// src/panel/render-chat.js
var WELCOME_SUGGESTIONS = [
  {
    label: "Turn off all lights at midnight",
    value: "Create an automation that turns off all lights at midnight",
    icon: "mdi:lightbulb-off-outline",
  },
  {
    label: "What devices do I have?",
    value: "What devices do I have and which ones are currently on?",
    icon: "mdi:devices",
  },
  {
    label: "Suggest automations for my home",
    value: "Suggest useful automations based on my devices and usage patterns",
    icon: "mdi:auto-fix",
  },
];
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
  const isEmpty = host._messages.length === 0;
  if (isEmpty) {
    return x`
      <div class="chat-pane">
        <div class="chat-welcome-center" id="chat-messages">
          ${i4(
            host._welcomeKey || 0,
            x`
              <div class="welcome-center-content">
                <img
                  src="/api/selora_ai/logo.png"
                  alt="Selora AI"
                  style="width:72px;height:72px;border-radius:16px;margin-bottom:16px;"
                />
                <div style="font-size:26px;font-weight:700;margin-bottom:6px;">
                  Welcome to
                  <span class="gold-text">Selora AI</span>
                </div>
                <div
                  style="font-size:15px;color:var(--secondary-text-color);margin-bottom:0;"
                >
                  Your intelligent home automation architect
                </div>

                ${
                  host._llmNeedsSetup
                    ? x`
                      <div
                        style="margin-top:16px;padding:24px;border-radius:14px;background:rgba(251,191,36,0.06);border:1.5px solid rgba(251,191,36,0.25);cursor:pointer;transition:border-color 0.2s,background 0.2s;max-width:380px;"
                        @click=${() => host._goToSettings()}
                      >
                        <ha-icon
                          icon="mdi:rocket-launch-outline"
                          style="--mdc-icon-size:32px;color:#fbbf24;margin-bottom:12px;"
                        ></ha-icon>
                        <div
                          style="font-size:16px;font-weight:700;margin-bottom:6px;"
                        >
                          Get started
                        </div>
                        <div
                          style="font-size:13px;opacity:0.6;margin-bottom:16px;"
                        >
                          Configure your LLM provider in the Settings tab to
                          start chatting with your home.
                        </div>
                        <span
                          style="display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:#fbbf24;"
                        >
                          Open Settings
                          <ha-icon
                            icon="mdi:arrow-right"
                            style="--mdc-icon-size:16px;"
                          ></ha-icon>
                        </span>
                      </div>
                    `
                    : x`
                      <div class="welcome-composer-area">
                        <selora-particles
                          class="welcome-composer-particles"
                          .count=${260}
                          .color=${host._isDark ? "#fbbf24" : host._primaryColor || "#03a9f4"}
                          .maxOpacity=${host._isDark ? 0.55 : 0.5}
                        ></selora-particles>
                        ${_renderComposer(host, { welcome: true })}
                      </div>

                      <details class="welcome-quickstart">
                        <summary class="welcome-quickstart-summary">
                          <span>Quick start</span>
                          <ha-icon
                            icon="mdi:chevron-down"
                            class="welcome-quickstart-chevron"
                          ></ha-icon>
                        </summary>
                        ${renderQuickActions(host, WELCOME_SUGGESTIONS)}
                      </details>
                    `
                }
              </div>
            `,
          )}
        </div>
      </div>
    `;
  }
  return x`
    <div class="chat-pane">
      <div class="chat-messages" id="chat-messages">
        ${host._messages.map((msg, idx) => renderMessage(host, msg, idx))}
        ${host._deviceDetail ? renderDeviceDetail(host) : ""}
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

      <div class="chat-input-wrapper">${_renderComposer(host)}</div>
    </div>
  `;
}
function _autoResize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
}
function _renderComposer(host, opts = {}) {
  const welcome = !!opts.welcome;
  return x`
    <div
      class="chat-input composer-styled ${welcome ? "composer-welcome" : ""}"
    >
      <textarea
        class="composer-textarea"
        .value=${host._input}
        @input=${(e5) => {
          host._input = e5.target.value;
          _autoResize(e5.target);
        }}
        @keydown=${(e5) => {
          if (e5.key === "Enter" && !e5.shiftKey) {
            e5.preventDefault();
            host._sendMessage();
            return;
          }
          if (e5.key === "ArrowUp" && !host._input) {
            const lastUser = [...host._messages]
              .reverse()
              .find((m2) => m2.role === "user" && m2.content);
            if (lastUser) {
              e5.preventDefault();
              host._input = lastUser.content;
              const ta = e5.target;
              requestAnimationFrame(() => {
                ta.value = lastUser.content;
                ta.setSelectionRange(ta.value.length, ta.value.length);
                _autoResize(ta);
              });
            }
          }
        }}
        @focus=${() => {
          requestAnimationFrame(() => host._requestScrollChat());
        }}
        placeholder="Ask Selora AI anything…"
        ?disabled=${host._loading || host._streaming}
        rows="1"
      ></textarea>
      ${
        host._streaming
          ? x`<button
            class="composer-send"
            @click=${() => host._stopStreaming()}
            title="Stop generating"
          >
            <ha-icon icon="mdi:stop"></ha-icon>
          </button>`
          : x`<button
            class="composer-send"
            @click=${() => host._sendMessage()}
            ?disabled=${host._loading || !host._input.trim()}
            title="Send"
          >
            <ha-icon icon="mdi:arrow-up"></ha-icon>
          </button>`
      }
    </div>
  `;
}
function renderMessage(host, msg, idx) {
  const isUser = msg.role === "user";
  if (msg._streaming && !msg.content) return x``;
  let displayContent = msg.content;
  let showAutomationSpinner = false;
  let showSceneSpinner = false;
  if (!isUser) {
    const { text, isPartialBlock, partialBlockType } = stripAutomationBlock(
      msg.content,
    );
    displayContent = text;
    showAutomationSpinner =
      isPartialBlock && msg._streaming && partialBlockType === "automation";
    showSceneSpinner =
      isPartialBlock && msg._streaming && partialBlockType === "scene";
    if (msg.automation_status === "refining" && msg.automation) {
      displayContent = "";
    }
  }
  return x`
    <div class="message-row">
      ${
        isUser
          ? x`
            <div class="bubble user">
              <span class="msg-content" .textContent=${msg.content}></span>
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
                  showSceneSpinner
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
                          >Building scene...</span
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
                ${msg.scene ? host._renderSceneCard(msg, idx) : ""}
                ${
                  msg._interrupted
                    ? x`
                      <div class="stream-interrupt">
                        <ha-icon
                          icon="mdi:alert-circle-outline"
                          style="--mdc-icon-size:16px;flex-shrink:0;"
                        ></ha-icon>
                        <span class="stream-interrupt-text"
                          >${msg._interruptReason || "Response was cut short."}</span
                        >
                      </div>
                    `
                    : ""
                }
              </div>
              ${
                msg.quick_actions &&
                msg.quick_actions.length &&
                idx === host._messages.length - 1
                  ? renderQuickActions(host, msg.quick_actions, {
                      used: !!msg._qa_used,
                    })
                  : ""
              }
              <div
                class="bubble-meta"
                style="display:flex;justify-content:space-between;align-items:center;width:100%;"
              >
                <span>
                  Selora AI · ${formatTime(msg.timestamp)}
                  ${
                    msg._interrupted && msg._retryWith
                      ? x` ·
                        <button
                          class="stream-interrupt-retry"
                          @click=${() => host._retryMessage(msg._retryWith)}
                        >
                          <ha-icon
                            icon="mdi:refresh"
                            style="--mdc-icon-size:12px;"
                          ></ha-icon>
                          Retry
                        </button>`
                      : ""
                  }
                </span>
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
var DOMAIN_ICONS = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  climate: "mdi:thermostat",
  lock: "mdi:lock",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  media_player: "mdi:speaker",
  vacuum: "mdi:robot-vacuum",
  sensor: "mdi:eye",
  binary_sensor: "mdi:motion-sensor",
  water_heater: "mdi:water-boiler",
  humidifier: "mdi:air-humidifier",
  camera: "mdi:cctv",
  device_tracker: "mdi:map-marker",
};
function _stateColor2(state) {
  if (!state) return "var(--selora-zinc-400)";
  const s6 = state.toLowerCase();
  if (
    [
      "on",
      "home",
      "open",
      "unlocked",
      "playing",
      "heating",
      "cooling",
      "cleaning",
    ].includes(s6)
  )
    return "var(--selora-accent)";
  if (
    [
      "off",
      "closed",
      "locked",
      "docked",
      "idle",
      "standby",
      "not_home",
      "paused",
    ].includes(s6)
  )
    return "var(--selora-zinc-400)";
  if (["unavailable", "unknown", "error", "jammed"].includes(s6))
    return "#ef4444";
  return "var(--selora-zinc-200)";
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
    return `${String(h3).padStart(2, "0")}:${String(m2).padStart(2, "0")}`;
  }
  const parts = s6.split(":");
  if (parts.length >= 2) {
    const h3 = parseInt(parts[0], 10);
    const m2 = parseInt(parts[1], 10);
    if (!isNaN(h3) && !isNaN(m2)) {
      return `${String(h3).padStart(2, "0")}:${String(m2).padStart(2, "0")}`;
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
    if (item.offset) {
      const neg = item.offset.startsWith("-");
      const raw = neg ? item.offset.slice(1) : item.offset;
      const [h3, m2, s6] = raw.split(":").map(Number);
      const parts = [];
      if (h3) parts.push(`${h3}h`);
      if (m2) parts.push(`${m2}min`);
      if (s6) parts.push(`${s6}s`);
      const label = parts.join(" ") || item.offset;
      return `${label} ${neg ? "before" : "after"} ${ev}`;
    }
    return `When it is ${ev}`;
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
    ((automationData.triggers ?? automationData.trigger)?.length ||
      (automationData.actions ?? automationData.action)?.length);
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
        <h3 style="flex:1;font-size:14px;margin:0;" title=${item.title}>
          ${item.title}
        </h3>
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
                  ?disabled=${host._loadingProactive || host._llmNeedsSetup}
                  title=${host._llmNeedsSetup ? "Configure an LLM provider first" : ""}
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
                  ?disabled=${host._generatingSuggestions || host._llmNeedsSetup}
                  title=${host._llmNeedsSetup ? "Configure an LLM provider first" : ""}
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
              ${visibleItems.map((item) =>
                renderSuggestionCard(host, item, bulkMode, selectedKeys),
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

// src/panel/stale-automations.js
var STALE_KEPT_KEY = "selora_stale_kept";
function _staleDays(host) {
  return host._config?.stale_days || 5;
}
function _staleMs(host) {
  return _staleDays(host) * 24 * 60 * 60 * 1e3;
}
function _loadKept() {
  try {
    return JSON.parse(localStorage.getItem(STALE_KEPT_KEY) || "{}");
  } catch {
    return {};
  }
}
function _saveKept(kept) {
  localStorage.setItem(STALE_KEPT_KEY, JSON.stringify(kept));
}
function keepAutomation(host, automationId) {
  const kept = _loadKept();
  kept[automationId] = Date.now();
  _saveKept(kept);
  host.requestUpdate();
}
function getStaleAutomations(host) {
  if (!host._automations?.length) return [];
  const now = Date.now();
  const staleMs = _staleMs(host);
  const cutoff = now - staleMs;
  const kept = _loadKept();
  let dirty = false;
  for (const [id, ts] of Object.entries(kept)) {
    if (now - ts > staleMs) {
      delete kept[id];
      dirty = true;
    }
  }
  if (dirty) _saveKept(kept);
  return host._automations.filter((a4) => {
    if (!host._automationIsEnabled(a4)) return false;
    if (!a4.automation_id?.startsWith("selora_ai_")) return false;
    if (kept[a4.automation_id]) return false;
    if (!a4.last_triggered) {
      if (a4.last_updated) {
        const created = new Date(a4.last_updated).getTime();
        if (created >= cutoff) return false;
      }
      return true;
    }
    return new Date(a4.last_triggered).getTime() < cutoff;
  });
}
function renderStaleModal(host) {
  if (!host._staleModalOpen) return "";
  const stale = getStaleAutomations(host);
  if (!stale.length) {
    host._staleModalOpen = false;
    return "";
  }
  const staleDays = _staleDays(host);
  const selected = host._staleSelected || {};
  const selectedCount = stale.filter((a4) => selected[a4.automation_id]).length;
  const allSelected = selectedCount === stale.length;
  const someSelected = selectedCount > 0 && !allSelected;
  return x`
    <div
      class="modal-overlay"
      @click=${() => {
        host._staleModalOpen = false;
        host._staleSelected = {};
      }}
    >
      <div
        class="modal-content"
        style="max-width:560px;max-height:80vh;display:flex;flex-direction:column;border:1px solid var(--selora-accent);"
        @click=${(e5) => e5.stopPropagation()}
      >
        <h3 class="modal-title" style="flex-shrink:0;">
          <ha-icon
            icon="mdi:clock-alert-outline"
            style="--mdc-icon-size:22px;color:#f59e0b;vertical-align:middle;margin-right:6px;"
          ></ha-icon>
          Stale Automations
          <span
            style="font-size:13px;font-weight:400;color:var(--secondary-text-color);margin-left:8px;"
            >${stale.length} automation${stale.length !== 1 ? "s" : ""}</span
          >
        </h3>
        <p
          style="font-size:14px;line-height:1.6;margin:0 0 4px;color:var(--primary-text-color);flex-shrink:0;"
        >
          The following Selora automations haven't triggered in ${staleDays}
          day${staleDays !== 1 ? "s" : ""}. You can remove ones you no longer
          need to free up space for new suggestions.
        </p>

        <!-- Select all + bulk actions -->
        <div
          style="display:flex;align-items:center;justify-content:space-between;margin:12px 0 4px;padding:0 2px;flex-shrink:0;"
        >
          <label
            style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--secondary-text-color);cursor:pointer;user-select:none;"
          >
            <input
              type="checkbox"
              .checked=${allSelected}
              .indeterminate=${someSelected}
              @change=${(e5) => {
                const next = {};
                if (e5.target.checked) {
                  stale.forEach((a4) => {
                    next[a4.automation_id] = true;
                  });
                }
                host._staleSelected = next;
              }}
            />
            Select all
          </label>
          ${
            selectedCount > 0
              ? x`<button
                class="modal-btn modal-cancel"
                style="font-size:11px;padding:4px 10px;color:#ef4444;border-color:#ef4444;"
                ?disabled=${host._staleBulkDeleting}
                @click=${async () => {
                  const toDelete = stale.filter(
                    (a4) => selected[a4.automation_id],
                  );
                  if (
                    !confirm(
                      `Remove ${toDelete.length} automation${toDelete.length !== 1 ? "s" : ""} permanently?`,
                    )
                  )
                    return;
                  host._staleBulkDeleting = true;
                  for (const a4 of toDelete) {
                    try {
                      await host.hass.callWS({
                        type: "selora_ai/delete_automation",
                        automation_id: a4.automation_id,
                      });
                    } catch (err) {
                      console.error("Failed to delete", a4.alias, err);
                    }
                  }
                  await host._loadAutomations();
                  host._staleSelected = {};
                  host._staleBulkDeleting = false;
                  host._showToast(
                    `Removed ${toDelete.length} automation${toDelete.length !== 1 ? "s" : ""}.`,
                    "success",
                  );
                  host.requestUpdate();
                }}
              >
                <ha-icon
                  icon="mdi:trash-can-outline"
                  style="--mdc-icon-size:13px;"
                ></ha-icon>
                Remove ${selectedCount} selected
              </button>`
              : ""
          }
        </div>

        <!-- Scrollable list -->
        <div
          style="flex:1;min-height:0;overflow-y:auto;border:1px solid var(--divider-color);border-radius:8px;"
        >
          ${stale.map(
            (a4) => x`
              <div
                style="display:flex;align-items:center;padding:10px 14px;border-bottom:1px solid var(--divider-color);gap:10px;"
              >
                <input
                  type="checkbox"
                  .checked=${!!selected[a4.automation_id]}
                  @change=${(e5) => {
                    const next = { ...host._staleSelected };
                    if (e5.target.checked) {
                      next[a4.automation_id] = true;
                    } else {
                      delete next[a4.automation_id];
                    }
                    host._staleSelected = next;
                  }}
                  @click=${(e5) => e5.stopPropagation()}
                  style="flex-shrink:0;cursor:pointer;"
                />
                <div
                  style="flex:1;min-width:0;cursor:pointer;"
                  @click=${() => {
                    host._staleDetailAuto = a4;
                  }}
                >
                  <div
                    style="font-size:13px;font-weight:600;color:var(--primary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                  >
                    ${a4.alias || a4.entity_id}
                  </div>
                  <div
                    style="font-size:11px;color:var(--secondary-text-color);margin-top:2px;"
                  >
                    Last triggered:
                    ${a4.last_triggered ? formatTimeAgo(a4.last_triggered) : "Never"}
                  </div>
                </div>
                <button
                  class="modal-btn modal-cancel"
                  style="flex-shrink:0;font-size:11px;padding:4px 10px;color:var(--selora-accent);border-color:var(--selora-accent);"
                  @click=${(e5) => {
                    e5.stopPropagation();
                    keepAutomation(host, a4.automation_id);
                    host._showToast(
                      `"${a4.alias || "Automation"}" kept for ${staleDays} days.`,
                      "info",
                    );
                  }}
                >
                  <ha-icon
                    icon="mdi:check"
                    style="--mdc-icon-size:13px;"
                  ></ha-icon>
                  Keep
                </button>
                <ha-icon
                  icon="mdi:chevron-right"
                  style="--mdc-icon-size:18px;color:var(--secondary-text-color);flex-shrink:0;cursor:pointer;"
                  @click=${() => {
                    host._staleDetailAuto = a4;
                  }}
                ></ha-icon>
              </div>
            `,
          )}
        </div>
        <div
          class="modal-actions"
          style="justify-content:center;gap:12px;margin-top:16px;flex-shrink:0;"
        >
          <button
            class="modal-btn modal-cancel"
            @click=${() => {
              host._staleModalOpen = false;
              host._staleSelected = {};
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
    ${_renderStaleDetailModal(host)}
  `;
}
function _renderStaleDetailModal(host) {
  const a4 = host._staleDetailAuto;
  if (!a4) return "";
  const staleDays = _staleDays(host);
  return x`
    <div
      class="modal-overlay"
      style="z-index:10002;"
      @click=${() => {
        host._staleDetailAuto = null;
      }}
    >
      <div
        class="modal-content"
        style="max-width:520px;border:1px solid var(--selora-accent);"
        @click=${(e5) => e5.stopPropagation()}
      >
        <h3 class="modal-title">
          <ha-icon
            icon="mdi:robot"
            style="--mdc-icon-size:22px;color:var(--selora-accent);vertical-align:middle;margin-right:6px;"
          ></ha-icon>
          ${a4.alias || a4.entity_id}
        </h3>
        <div
          style="font-size:12px;color:var(--secondary-text-color);margin-bottom:12px;"
        >
          Last triggered:
          ${a4.last_triggered ? formatTimeAgo(a4.last_triggered) : "Never"} ·
          State: ${a4.state || "unknown"}
        </div>

        ${
          a4.description
            ? x`<p
              style="font-size:13px;margin:0 0 12px;color:var(--primary-text-color);"
            >
              ${a4.description}
            </p>`
            : ""
        }
        ${renderAutomationFlowchart(host, a4)}

        <div
          class="modal-actions"
          style="justify-content:center;gap:12px;margin-top:16px;"
        >
          <button
            class="modal-btn modal-cancel"
            @click=${() => {
              host._staleDetailAuto = null;
            }}
          >
            Back
          </button>
          <button
            class="modal-btn modal-cancel"
            style="color:var(--selora-accent);border-color:var(--selora-accent);"
            @click=${() => {
              keepAutomation(host, a4.automation_id);
              host._staleDetailAuto = null;
              host._showToast(
                `"${a4.alias || "Automation"}" kept for ${staleDays} days.`,
                "info",
              );
            }}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Keep
          </button>
          <button
            class="modal-btn modal-cancel"
            style="color:#ef4444;border-color:#ef4444;"
            @click=${async () => {
              if (!a4.automation_id) return;
              if (!confirm("Remove this automation permanently?")) return;
              try {
                await host.hass.callWS({
                  type: "selora_ai/delete_automation",
                  automation_id: a4.automation_id,
                });
                await host._loadAutomations();
                host._showToast("Automation removed.", "success");
              } catch (err) {
                host._showToast("Failed to remove: " + err.message, "error");
              }
              host._staleDetailAuto = null;
              host.requestUpdate();
            }}
          >
            <ha-icon
              icon="mdi:trash-can-outline"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            Remove
          </button>
        </div>
      </div>
    </div>
  `;
}

// src/panel/render-automations.js
function _cardHeader(name, badge) {
  return x`
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <ha-icon
        icon="mdi:robot"
        style="color:var(--primary-text-color);--mdc-icon-size:18px;display:flex;flex-shrink:0;"
      ></ha-icon>
      <span
        style="font-weight:700;font-size:14px;color:var(--primary-text-color);"
        >${name}</span
      >
      <span
        style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--selora-accent);color:#000;padding:2px 8px;border-radius:4px;"
        >${badge}</span
      >
    </div>
  `;
}
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
      <div style="margin-top:12px;padding:14px 0 0;">
        ${_cardHeader(automation.alias, "Being Refined")}
        <div class="proposal-body" style="padding:0;">
          ${renderAutomationFlowchart(host, automation)}
        </div>
      </div>
    `;
  }
  const yamlOpen = host._yamlOpen && host._yamlOpen[msgIndex];
  const yamlKey = `proposal_${msgIndex}`;
  const hasEdits =
    host._editedYaml[yamlKey] !== void 0 && host._editedYaml[yamlKey] !== yaml;
  return x`
    <div style="margin-top:12px;padding:14px 0 0;">
      ${_cardHeader(automation.alias, "Proposal")}
      <div class="proposal-body" style="padding:0;">
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

        <div
          class="yaml-toggle"
          style="margin-top:12px;"
          @click=${() => toggleYaml(host, msgIndex)}
        >
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen ? "Hide YAML" : "Edit YAML"}
        </div>
        ${
          yamlOpen
            ? x`<div style="margin-top:6px;">
              ${host._renderYamlEditor(yamlKey, yaml)}
              ${
                hasEdits
                  ? x`<div class="proposal-verify">
                    Your YAML edits will be used when you accept.
                  </div>`
                  : ""
              }
            </div>`
            : ""
        }
        <div style="display:flex;justify-content:flex-end;margin-top:12px;">
          <button
            class="btn btn-success"
            @click=${() => host._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Accept &amp; Save
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
  if (statusFilter === "enabled") {
    filteredAutomations = filteredAutomations.filter((a4) =>
      host._automationIsEnabled(a4),
    );
  } else if (statusFilter === "disabled") {
    filteredAutomations = filteredAutomations.filter(
      (a4) => !host._automationIsEnabled(a4),
    );
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
    (a4) => !host._automationIsEnabled(a4),
  ).length;
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
                  ${["all", "enabled", "disabled"].map(
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
                    class="btn btn-accent"
                    style="white-space:nowrap;"
                    ?disabled=${host._llmNeedsSetup}
                    title=${host._llmNeedsSetup ? "Configure an LLM provider first" : ""}
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
                  (${enabledCount} enabled, ${disabledCount} disabled)
                  ${(() => {
                    const staleCount = getStaleAutomations(host).length;
                    return staleCount > 0
                      ? x`<span
                          class="needs-attention-pill"
                          style="margin-left:8px;cursor:pointer;"
                          @click=${(e5) => {
                            e5.stopPropagation();
                            host._staleModalOpen = true;
                          }}
                          >${staleCount} stale</span
                        >`
                      : "";
                  })()}
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
                          ${host._bulkActionInProgress ? "Working\u2026" : "Delete selected"}
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
                  const isUnavailable = a4.state === "unavailable";
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
                              (a4.triggers ?? a4.trigger)?.length ||
                              (a4.actions ?? a4.action)?.length
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
                        <ha-icon
                          icon="mdi:robot"
                          style="--mdc-icon-size:18px;color:var(--selora-accent);flex-shrink:0;"
                        ></ha-icon>
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
                              : x`<div class="auto-row-title-row">
                                <span class="auto-row-title">${a4.alias}</span
                                >${
                                  isUnavailable
                                    ? x`<span
                                      class="needs-attention-pill"
                                      @click=${(e5) => {
                                        e5.stopPropagation();
                                        host._unavailableAutoId = automationId;
                                        host._unavailableAutoName = a4.alias;
                                      }}
                                      >Needs attention</span
                                    >`
                                    : ""
                                }
                              </div>`
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
                                            host._deleteAutomation(
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
                                  (a4.triggers ?? a4.trigger)?.length ||
                                  (a4.actions ?? a4.action)?.length
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
                              ${host._cardActiveTab[a4.entity_id] === "flow" && ((a4.triggers ?? a4.trigger)?.length || (a4.actions ?? a4.action)?.length) ? renderAutomationFlowchart(host, a4) : ""}
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
                class="btn btn-accent"
                ?disabled=${host._llmNeedsSetup}
                title=${host._llmNeedsSetup ? "Configure an LLM provider first" : ""}
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
      ${renderUnavailableModal(host)} ${renderStaleModal(host)}
    </div>
  `;
}
function renderUnavailableModal(host) {
  if (!host._unavailableAutoId) return "";
  return x`
    <div
      class="modal-overlay"
      @click=${() => {
        host._unavailableAutoId = null;
        host._unavailableAutoName = null;
      }}
    >
      <div
        class="modal-content"
        style="max-width:440px;border:1px solid var(--selora-accent);"
        @click=${(e5) => e5.stopPropagation()}
      >
        <h3 class="modal-title">
          <ha-icon
            icon="mdi:alert-circle-outline"
            style="--mdc-icon-size:22px;color:#ef4444;vertical-align:middle;margin-right:6px;"
          ></ha-icon>
          Automation Unavailable
        </h3>
        <p
          style="font-size:14px;line-height:1.6;margin:0 0 8px;color:var(--primary-text-color);"
        >
          <strong>${host._unavailableAutoName || "This automation"}</strong>
          is marked as unavailable by Home Assistant. This usually means:
        </p>
        <ul
          style="font-size:13px;line-height:1.8;margin:0 0 16px;padding-left:20px;color:var(--secondary-text-color);"
        >
          <li>
            A trigger or condition references an entity that no longer exists
          </li>
          <li>The automation YAML has a configuration error</li>
          <li>A required integration was removed or is not loaded</li>
        </ul>
        <p
          style="font-size:13px;margin:0 0 16px;color:var(--secondary-text-color);"
        >
          Open the automation in Home Assistant Settings to review and fix the
          configuration.
        </p>
        <div class="modal-actions" style="justify-content:center;gap:12px;">
          <button
            class="modal-btn modal-cancel"
            @click=${() => {
              host._unavailableAutoId = null;
              host._unavailableAutoName = null;
            }}
          >
            Close
          </button>
          <a
            class="modal-btn modal-create"
            href="/developer-tools/state"
            style="text-decoration:none;"
            @click=${() => {
              host._unavailableAutoId = null;
              host._unavailableAutoName = null;
            }}
          >
            <ha-icon
              icon="mdi:code-tags"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            Edit States
          </a>
          <a
            class="modal-btn modal-create"
            href="/config/automation/dashboard"
            style="text-decoration:none;"
            @click=${() => {
              host._unavailableAutoId = null;
              host._unavailableAutoName = null;
            }}
          >
            <ha-icon icon="mdi:robot" style="--mdc-icon-size:14px;"></ha-icon>
            Open in Automations
          </a>
        </div>
      </div>
    </div>
  `;
}

// src/panel/render-scenes.js
function _sceneCardHeader(name, badge) {
  return x`
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <ha-icon
        icon="mdi:palette"
        style="color:var(--primary-text-color);--mdc-icon-size:18px;display:flex;flex-shrink:0;"
      ></ha-icon>
      <span
        style="font-weight:700;font-size:14px;color:var(--primary-text-color);"
        >${name}</span
      >
      <span
        style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--selora-accent);color:#000;padding:2px 8px;border-radius:4px;"
        >${badge}</span
      >
    </div>
  `;
}
function _formatBrightness(val) {
  if (val == null) return null;
  const num = Number(val);
  if (isNaN(num)) return null;
  return `${Math.round((num / 255) * 100)}%`;
}
function _formatPosition(val) {
  if (val == null) return null;
  const num = Number(val);
  if (isNaN(num)) return null;
  return `${Math.round(num)}%`;
}
function _formatEntityAttrs(stateData) {
  const parts = [];
  const brightness =
    stateData.brightness_pct != null
      ? `${Math.round(Number(stateData.brightness_pct))}%`
      : _formatBrightness(stateData.brightness);
  if (brightness) parts.push(brightness);
  if (stateData.color_temp != null)
    parts.push(`${stateData.color_temp} mireds`);
  if (stateData.temperature != null) parts.push(`${stateData.temperature}\xB0`);
  const position = _formatPosition(
    stateData.position ?? stateData.current_position,
  );
  if (position) parts.push(position);
  const fanSpeed = _formatPosition(stateData.percentage);
  if (fanSpeed) parts.push(fanSpeed);
  if (stateData.volume_level != null)
    parts.push(`vol ${Math.round(stateData.volume_level * 100)}%`);
  if (stateData.source != null) parts.push(stateData.source);
  return parts.join(" \xB7 ");
}
function _renderEntityList(host, entities) {
  const entries = Object.entries(entities);
  if (!entries.length) return "";
  return x`
    <div class="scene-entity-list">
      ${entries.map(([entityId, stateData]) => {
        const domain = entityId.split(".")[0];
        const icon = DOMAIN_ICONS[domain] || "mdi:devices";
        const state = stateData.state || "unknown";
        const attrs = _formatEntityAttrs(stateData);
        const name = fmtEntity(host.hass, entityId);
        return x`
          <div class="scene-entity-row">
            <div class="scene-entity-name">
              <ha-icon
                icon=${icon}
                style="--mdc-icon-size:16px;color:var(--selora-accent);"
              ></ha-icon>
              <span>${name}</span>
            </div>
            <div class="scene-entity-state">
              ${attrs ? x`<span class="scene-entity-attr">${attrs}</span>` : ""}
              <span style="color:${_stateColor2(state)};">${state}</span>
            </div>
          </div>
        `;
      })}
    </div>
  `;
}
function renderSceneCard(host, msg, msgIndex) {
  const scene = msg.scene;
  if (!scene) return "";
  const status = msg.scene_status || (msg.scene_id ? "saved" : void 0);
  const yamlKey = `scene_${msgIndex}`;
  const yamlOpen = host._yamlOpen && host._yamlOpen[yamlKey];
  if (status === "saved") {
    return x`
      <div class="proposal-card" style="margin-top:12px;">
        <div class="proposal-header">
          <ha-icon icon="mdi:check-circle"></ha-icon>
          Scene Created
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${scene.name}</div>
          <div class="proposal-status saved">
            <ha-icon icon="mdi:check"></ha-icon> Saved to Home Assistant
          </div>
          <div class="proposal-actions">
            <button
              class="btn btn-success"
              @click=${() => {
                const id = msg.entity_id
                  ? msg.entity_id.replace(/^scene\./, "")
                  : msg.scene_id;
                host._activateScene(id, scene.name);
              }}
            >
              <ha-icon icon="mdi:play" style="--mdc-icon-size:14px;"></ha-icon>
              Activate
            </button>
            <button
              class="btn btn-outline"
              @click=${() => {
                window.history.pushState(null, "", "/config/scene/dashboard");
                window.dispatchEvent(new Event("location-changed"));
              }}
            >
              <ha-icon
                icon="mdi:open-in-new"
                style="--mdc-icon-size:14px;"
              ></ha-icon>
              View in HA
            </button>
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
          Scene Declined
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${scene.name}</div>
          <div class="proposal-status declined">
            Dismissed. You can refine it by replying below.
          </div>
        </div>
      </div>
    `;
  }
  if (status === "refining") {
    return x`
      <div style="margin-top:12px;padding:14px 0 0;">
        ${_sceneCardHeader(scene.name, "Being Refined")}
        <div class="proposal-body" style="padding:0;">
          ${_renderEntityList(host, scene.entities || {})}
          ${
            msg.scene_yaml
              ? x`<div
                class="yaml-toggle"
                style="margin-top:10px;margin-bottom:0;"
                @click=${() => toggleYaml(host, yamlKey)}
              >
                <ha-icon
                  icon="mdi:code-braces"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${yamlOpen ? "Hide YAML" : "View YAML"}
              </div>`
              : ""
          }
          ${
            yamlOpen && msg.scene_yaml
              ? x`
                <ha-code-editor
                  mode="yaml"
                  .value=${msg.scene_yaml}
                  read-only
                  style="--code-mirror-font-size:12px;margin-top:10px;"
                ></ha-code-editor>
              `
              : ""
          }
        </div>
      </div>
    `;
  }
  return x`
    <div style="margin-top:12px;padding:14px 0 0;">
      ${_sceneCardHeader(scene.name, "Proposal")}
      <div class="proposal-body" style="padding:0;">
        ${_renderEntityList(host, scene.entities || {})}

        <div
          class="yaml-toggle"
          style="margin-top:12px;"
          @click=${() => toggleYaml(host, yamlKey)}
        >
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen ? "Hide YAML" : "View YAML"}
        </div>
        ${
          yamlOpen && msg.scene_yaml
            ? x`
              <ha-code-editor
                mode="yaml"
                .value=${msg.scene_yaml}
                read-only
                style="--code-mirror-font-size:12px;margin-top:6px;"
              ></ha-code-editor>
            `
            : ""
        }
        <div style="display:flex;justify-content:flex-end;margin-top:12px;">
          <button
            class="btn btn-success"
            @click=${() => host._acceptScene(msgIndex)}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Accept &amp; Save
          </button>
        </div>
      </div>
    </div>
  `;
}
function _sceneEntityCount(scene) {
  if (typeof scene.entity_count === "number") return scene.entity_count;
  return Object.keys(scene.entities || {}).length;
}
function renderScenes(host) {
  const filterText = (host._sceneFilter || "").toLowerCase();
  const sortBy = host._sceneSortBy || "recent";
  let filtered = [...(host._scenes || [])];
  if (filterText) {
    filtered = filtered.filter((s6) =>
      (s6.name || "").toLowerCase().includes(filterText),
    );
  }
  if (sortBy === "recent") {
    filtered.sort((a4, b2) => {
      const at = a4.updated_at ? new Date(a4.updated_at).getTime() : 0;
      const bt = b2.updated_at ? new Date(b2.updated_at).getTime() : 0;
      return bt - at;
    });
  } else if (sortBy === "alpha") {
    filtered.sort((a4, b2) => (a4.name || "").localeCompare(b2.name || ""));
  } else if (sortBy === "size") {
    filtered.sort((a4, b2) => (b2.entity_count || 0) - (a4.entity_count || 0));
  }
  return x`
    <div class="scroll-view">
      <div class="section-card">
        <div class="section-card-header">
          <h3>Your Scenes</h3>
        </div>
        ${
          (host._scenes || []).length > 0
            ? x`
              <div class="filter-row" style="margin-top:12px;">
                <div class="filter-input-wrap" style="flex:0 1 260px;">
                  <ha-icon icon="mdi:magnify"></ha-icon>
                  <input
                    type="text"
                    placeholder="Filter scenes…"
                    .value=${host._sceneFilter || ""}
                    @input=${(e5) => {
                      host._sceneFilter = e5.target.value;
                    }}
                  />
                  ${
                    host._sceneFilter
                      ? x`<ha-icon
                        icon="mdi:close-circle"
                        style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                        @click=${() => {
                          host._sceneFilter = "";
                        }}
                      ></ha-icon>`
                      : ""
                  }
                </div>
                <select
                  class="sort-select"
                  .value=${host._sceneSortBy || "recent"}
                  @change=${(e5) => {
                    host._sceneSortBy = e5.target.value;
                  }}
                >
                  <option value="recent">Recently updated</option>
                  <option value="alpha">Alphabetical</option>
                  <option value="size">Most entities</option>
                </select>
                <div
                  style="margin-left:auto;display:flex;align-items:center;gap:8px;"
                >
                  <button
                    class="btn btn-accent"
                    style="white-space:nowrap;"
                    ?disabled=${host._llmNeedsSetup}
                    title=${host._llmNeedsSetup ? "Configure an LLM provider first" : ""}
                    @click=${() => host._newSceneChat()}
                  >
                    <ha-icon
                      icon="mdi:plus"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    New Scene
                  </button>
                </div>
              </div>
              <div class="automations-summary">
                ${filtered.length} scene${filtered.length !== 1 ? "s" : ""}
              </div>
              <div class="automations-list">
                ${filtered.map((s6) => {
                  const sceneId = s6.scene_id;
                  const sceneEntityId = s6.entity_id;
                  const entities = s6.entities || {};
                  const entityCount = _sceneEntityCount(s6);
                  const isExpanded = !!host._expandedScenes?.[sceneId];
                  const yamlOpen = !!host._sceneYamlOpen?.[sceneId];
                  const burgerOpen = host._openSceneBurger === sceneId;
                  const deleting = !!host._deletingScene?.[sceneId];
                  const loadingChat = !!host._loadingToChat?.[sceneId];
                  const updated = formatTimeAgo(s6.updated_at);
                  const meta = `${entityCount} entit${entityCount === 1 ? "y" : "ies"}${updated ? ` \xB7 updated ${updated}` : ""}`;
                  const isSelora = s6.source === "selora";
                  return x`
                    <div
                      class="auto-row${isExpanded ? " expanded" : ""}"
                      data-scene-id="${sceneId}"
                    >
                      <div
                        class="auto-row-main"
                        @click=${(e5) => {
                          if (
                            e5.target.closest(
                              ".burger-menu-wrapper, .burger-dropdown, .burger-item, .btn",
                            )
                          )
                            return;
                          host._expandedScenes = {
                            ...host._expandedScenes,
                            [sceneId]: !isExpanded,
                          };
                        }}
                      >
                        <div
                          style="display:flex;flex-direction:column;align-items:center;gap:4px;flex-shrink:0;"
                        >
                          <ha-icon
                            icon="mdi:palette"
                            style="--mdc-icon-size:18px;color:var(--selora-accent);"
                          ></ha-icon>
                          ${
                            !isSelora && host.narrow
                              ? x`<span
                                style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--secondary-background-color);color:var(--secondary-text-color);padding:1px 4px;border-radius:3px;"
                                >HA</span
                              >`
                              : ""
                          }
                        </div>
                        <div class="auto-row-name">
                          <div class="auto-row-title-row">
                            <span class="auto-row-title">${s6.name}</span>
                            ${
                              !isSelora && !host.narrow
                                ? x`<span
                                  style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--secondary-background-color);color:var(--secondary-text-color);padding:2px 6px;border-radius:4px;flex-shrink:0;"
                                  >HA</span
                                >`
                                : ""
                            }
                          </div>
                          <span class="auto-row-desc auto-row-desc--meta-only"
                            >${meta}</span
                          >
                          <span class="auto-row-mobile-meta">
                            <span>${meta}</span>
                            <ha-icon
                              icon="mdi:chevron-down"
                              class="card-chevron ${isExpanded ? "open" : ""}"
                              style="--mdc-icon-size:16px;"
                            ></ha-icon>
                          </span>
                        </div>
                        <div
                          style="display:flex;align-items:center;gap:8px;flex-shrink:0;"
                        >
                          <button
                            class="btn btn-outline"
                            style="padding:4px 10px;height:28px;font-size:13px;"
                            ?disabled=${!sceneEntityId}
                            @click=${(e5) => {
                              e5.stopPropagation();
                              const id = sceneEntityId
                                ? sceneEntityId.replace(/^scene\./, "")
                                : sceneId;
                              host._activateScene(id, s6.name);
                            }}
                            title="Activate scene"
                          >
                            <ha-icon
                              icon="mdi:play"
                              style="--mdc-icon-size:14px;"
                            ></ha-icon>
                            Activate
                          </button>
                          <div class="burger-menu-wrapper">
                            <button
                              class="burger-btn"
                              @click=${(e5) => {
                                e5.stopPropagation();
                                host._openSceneBurger = burgerOpen
                                  ? null
                                  : sceneId;
                              }}
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
                                      ?disabled=${loadingChat}
                                      @click=${(e5) => {
                                        e5.stopPropagation();
                                        host._openSceneBurger = null;
                                        host._loadSceneToChat(sceneId);
                                      }}
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
                                        host._openSceneBurger = null;
                                        if (sceneEntityId) {
                                          host.dispatchEvent(
                                            new CustomEvent("hass-more-info", {
                                              bubbles: true,
                                              composed: true,
                                              detail: {
                                                entityId: sceneEntityId,
                                              },
                                            }),
                                          );
                                        } else {
                                          window.history.pushState(
                                            null,
                                            "",
                                            "/config/scene/dashboard",
                                          );
                                          window.dispatchEvent(
                                            new Event("location-changed"),
                                          );
                                        }
                                      }}
                                    >
                                      <ha-icon
                                        icon="mdi:open-in-new"
                                        style="--mdc-icon-size:14px;"
                                      ></ha-icon>
                                      Open in HA
                                    </button>
                                    ${
                                      isSelora
                                        ? x`<button
                                          class="burger-item danger"
                                          ?disabled=${deleting}
                                          @click=${(e5) => {
                                            e5.stopPropagation();
                                            host._openSceneBurger = null;
                                            host._deleteSceneConfirmId =
                                              sceneId;
                                            host._deleteSceneConfirmName =
                                              s6.name;
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:trash-can-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${deleting ? "Deleting\u2026" : "Delete"}
                                        </button>`
                                        : ""
                                    }
                                  </div>
                                `
                                : ""
                            }
                          </div>
                        </div>
                      </div>
                      ${
                        isExpanded
                          ? x`
                            <div class="auto-row-expand">
                              ${
                                Object.keys(entities).length
                                  ? _renderEntityList(host, entities)
                                  : x`<div
                                    style="font-size:12px;opacity:0.6;padding:6px 0;"
                                  >
                                    No entity details available — open the scene
                                    in Home Assistant to inspect it.
                                  </div>`
                              }
                              <div
                                class="yaml-toggle"
                                style="margin-top:10px;"
                                @click=${() => {
                                  host._sceneYamlOpen = {
                                    ...host._sceneYamlOpen,
                                    [sceneId]: !yamlOpen,
                                  };
                                }}
                              >
                                <ha-icon
                                  icon="mdi:code-braces"
                                  style="--mdc-icon-size:14px;"
                                ></ha-icon>
                                ${yamlOpen ? "Hide YAML" : "View YAML"}
                              </div>
                              ${
                                yamlOpen
                                  ? x`
                                    <ha-code-editor
                                      mode="yaml"
                                      .value=${s6.yaml || "# YAML not available \u2014 open the scene in Home Assistant to view it."}
                                      read-only
                                      style="--code-mirror-font-size:12px;"
                                    ></ha-code-editor>
                                  `
                                  : ""
                              }
                            </div>
                          `
                          : ""
                      }
                    </div>
                  `;
                })}
              </div>
              ${
                filtered.length === 0 && (host._scenes || []).length > 0
                  ? x`<div
                    style="text-align:center;opacity:0.45;padding:24px 0;"
                  >
                    No scenes match "${host._sceneFilter}"
                  </div>`
                  : ""
              }
            `
            : x`<div style="text-align:center;padding:32px 0;">
              <ha-icon
                icon="mdi:palette"
                style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"
              ></ha-icon>
              <p style="opacity:0.45;margin:0 0 12px;">
                No scenes found. Ask Selora to create one.
              </p>
              <button
                class="btn btn-accent"
                ?disabled=${host._llmNeedsSetup}
                title=${host._llmNeedsSetup ? "Configure an LLM provider first" : ""}
                @click=${() => host._newSceneChat()}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                New Scene
              </button>
            </div>`
        }
      </div>
      ${renderDeleteSceneModal(host)}
    </div>
  `;
}
function renderDeleteSceneModal(host) {
  if (!host._deleteSceneConfirmId) return "";
  const name = host._deleteSceneConfirmName || "this scene";
  return x`
    <div
      class="modal-overlay"
      @click=${(e5) => {
        if (e5.target === e5.currentTarget) {
          host._deleteSceneConfirmId = null;
          host._deleteSceneConfirmName = null;
        }
      }}
    >
      <div class="modal-content" style="max-width:420px;text-align:center;">
        <div style="font-size:17px;font-weight:600;margin-bottom:8px;">
          Delete Scene
        </div>
        <div style="font-size:13px;opacity:0.7;margin-bottom:20px;">
          Delete <strong>${name}</strong>? This removes the scene from Home
          Assistant and cannot be undone.
        </div>
        <div style="display:flex;gap:10px;justify-content:center;">
          <button
            class="btn btn-outline"
            @click=${() => {
              host._deleteSceneConfirmId = null;
              host._deleteSceneConfirmName = null;
            }}
          >
            Cancel
          </button>
          <button
            class="btn"
            style="background:#ef4444;color:#fff;border-color:#ef4444;"
            @click=${() => host._confirmDeleteScene()}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  `;
}

// src/panel/render-settings.js
function _textInput({
  label,
  value,
  oninput,
  type = "text",
  placeholder = "",
  style = "",
}) {
  return x`
    ${
      label
        ? x`<label
          style="font-size:13px;color:var(--secondary-text-color);display:block;margin-bottom:6px;"
          >${label}</label
        >`
        : ""
    }
    <input
      class="form-select"
      type=${type}
      .value=${value || ""}
      @input=${oninput}
      placeholder=${placeholder}
      style="width:100%;box-sizing:border-box;${style}"
    />
  `;
}
function _todayCostHint(host) {
  const states = host.hass?.states || {};
  for (const [entityId, state] of Object.entries(states)) {
    if (
      entityId.startsWith("sensor.") &&
      entityId.includes("selora") &&
      entityId.endsWith("llm_cost")
    ) {
      const v2 = Number(state?.state);
      if (Number.isFinite(v2) && v2 > 0) {
        return v2;
      }
      return 0;
    }
  }
  return null;
}
function _renderUsageHeaderLink(host) {
  const cost = _todayCostHint(host);
  const hasData = cost !== null && cost > 0;
  return x`
    <button
      class="section-card-action"
      title="View token usage"
      @click=${() => {
        host._activeTab = "usage";
        host._loadUsageStats?.();
        host.requestUpdate();
      }}
    >
      <ha-icon icon="mdi:chart-line-variant"></ha-icon>
      <span>${hasData ? "Usage" : "View usage"}</span>
      <ha-icon
        icon="mdi:chevron-right"
        class="section-card-action-chevron"
      ></ha-icon>
    </button>
  `;
}
var _PROVIDERS = [
  { value: "selora_cloud", label: "Selora AI Cloud" },
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "gemini", label: "Google Gemini" },
  { value: "openai", label: "OpenAI (ChatGPT)" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "ollama", label: "Ollama (Local)" },
  {
    value: "selora_local",
    label: "Selora AI Local (On-device) \u2014 coming soon",
    disabled: true,
  },
];
function _renderProviderPicker(host) {
  const current = _PROVIDERS.find(
    (p2) => p2.value === host._config.llm_provider,
  );
  const open = host._providerDropdownOpen || false;
  return x`
    <div style="position:relative;">
      <button
        class="form-select"
        style="text-align:left;width:100%;display:flex;align-items:center;justify-content:space-between;"
        @click=${() => {
          host._providerDropdownOpen = !open;
          host.requestUpdate();
        }}
      >
        <span>${current ? current.label : "Select..."}</span>
        <ha-icon
          icon="mdi:chevron-down"
          style="--mdc-icon-size:18px;opacity:0.6;"
        ></ha-icon>
      </button>
      ${
        open
          ? x`
            <div
              style="position:fixed;inset:0;z-index:9;"
              @click=${() => {
                host._providerDropdownOpen = false;
                host.requestUpdate();
              }}
            ></div>
            <div
              style="position:absolute;top:100%;left:0;right:0;z-index:10;margin-top:4px;border-radius:10px;border:1px solid var(--divider-color);background:var(--card-background-color);box-shadow:0 4px 12px rgba(0,0,0,0.15);overflow:hidden;"
            >
              ${_PROVIDERS.map(
                (p2) => x`
                  <button
                    style="display:block;width:100%;text-align:left;padding:10px 14px;border:none;background:${p2.value === host._config.llm_provider ? "var(--selora-accent)" : "transparent"};color:${p2.disabled ? "var(--disabled-text-color, #999)" : p2.value === host._config.llm_provider ? "#000" : "var(--primary-text-color)"};font-size:14px;cursor:${p2.disabled ? "default" : "pointer"};opacity:${p2.disabled ? "0.5" : "1"};"
                    @click=${() => {
                      if (p2.disabled) return;
                      host._providerDropdownOpen = false;
                      host._updateConfig("llm_provider", p2.value);
                      host._showApiKeyInput = false;
                      host._newApiKey = "";
                      host._llmSaveStatus = null;
                    }}
                  >
                    ${p2.label}
                  </button>
                `,
              )}
            </div>
          `
          : ""
      }
    </div>
  `;
}
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
  const isSeloraCloud = host._config.llm_provider === "selora_cloud";
  const isAnthropic = host._config.llm_provider === "anthropic";
  const isGemini = host._config.llm_provider === "gemini";
  const isOpenAI = host._config.llm_provider === "openai";
  const isOpenRouter = host._config.llm_provider === "openrouter";
  const isSeloraLocal = host._config.llm_provider === "selora_local";
  return x`
    <div class="scroll-view">
      <div class="settings-form">
        <a
          href="https://selorahomes.com/docs/selora-ai/configuration/"
          target="_blank"
          rel="noopener noreferrer"
          class="settings-doc-banner"
        >
          <div style="flex:1;">
            <strong>Configuration guide</strong>
            <span
              >Learn how to set up LLM providers, remote access, and MCP
              tokens.</span
            >
          </div>
          <ha-icon
            icon="mdi:open-in-new"
            style="--mdc-icon-size:16px;flex-shrink:0;opacity:0.4;"
          ></ha-icon>
        </a>
        <div class="section-card settings-section">
          <div class="section-card-header section-card-header--with-action">
            <h3>LLM Provider</h3>
            ${_renderUsageHeaderLink(host)}
          </div>
          <div class="form-group">
            <label>Provider</label>
            ${_renderProviderPicker(host)}
          </div>

          ${
            isSeloraCloud
              ? x`
                <div class="form-group">
                  <label>Selora account</label>
                  ${
                    host._config.aigateway_linked
                      ? x`
                        <div
                          style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);"
                        >
                          <ha-icon
                            icon="mdi:check-circle"
                            style="--mdc-icon-size:18px;color:var(--success-color, #22c55e);flex-shrink:0;"
                          ></ha-icon>
                          <div style="flex:1;min-width:0;">
                            <div
                              style="font-size:14px;color:var(--primary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                            >
                              Linked${
                                host._config.aigateway_user_email
                                  ? x` as
                                    <strong
                                      >${host._config.aigateway_user_email}</strong
                                    >`
                                  : ""
                              }
                            </div>
                            <div
                              style="font-size:12px;color:var(--secondary-text-color);"
                            >
                              Selora Cloud is providing your LLM backend.
                            </div>
                          </div>
                          <button
                            class="btn btn-outline"
                            style="flex-shrink:0;"
                            @click=${() => host._unlinkAIGateway()}
                          >
                            Unlink
                          </button>
                        </div>
                      `
                      : x`
                        <div
                          style="display:flex;flex-direction:column;gap:10px;"
                        >
                          <p
                            style="font-size:13px;color:var(--secondary-text-color);margin:0;"
                          >
                            Sign in with your Selora account to use the hosted
                            LLM backend. No API key required.
                          </p>
                          ${
                            host._config.developer_mode
                              ? x`
                                ${_textInput({
                                  label: "Selora Cloud URL",
                                  value:
                                    host._config.selora_connect_url ||
                                    "https://connect.selorahomes.com",
                                  oninput: (e5) =>
                                    host._updateConfig(
                                      "selora_connect_url",
                                      e5.target.value,
                                    ),
                                })}
                                <div
                                  style="font-size:12px;color:var(--secondary-text-color);margin-top:-2px;"
                                >
                                  OAuth and chat completions both use this URL.
                                  Saved automatically when you link.
                                </div>
                              `
                              : ""
                          }
                          ${
                            host._aigwAuthorizeUrl
                              ? x`<a
                                class="btn btn-primary"
                                href=${host._aigwAuthorizeUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                style="align-self:flex-start;text-decoration:none;display:inline-flex;align-items:center;gap:6px;"
                              >
                                Open sign-in page →
                              </a>`
                              : x`<button
                                class="btn btn-primary"
                                ?disabled=${host._linkingAIGateway}
                                @click=${() => host._startAIGatewayLink()}
                                style="align-self:flex-start;"
                              >
                                ${
                                  host._linkingAIGateway
                                    ? x`<span
                                        class="spinner"
                                        style="width:14px;height:14px;"
                                      ></span>
                                      Preparing…`
                                    : "Link Selora account"
                                }
                              </button>`
                          }
                          ${
                            host._aigwAuthorizeUrl
                              ? x`<div
                                style="font-size:12px;color:var(--secondary-text-color);margin-top:4px;"
                              >
                                Opens in a new tab. After signing in, return to
                                this page — the panel updates automatically.
                              </div>`
                              : ""
                          }
                        </div>
                      `
                  }
                  ${
                    host._aigatewayError
                      ? x`<div
                        style="color:var(--error-color,#d32f2f);font-size:13px;padding:6px 0 0;"
                      >
                        ${host._aigatewayError}
                      </div>`
                      : ""
                  }
                </div>
                ${
                  host._config.aigateway_linked && host._config.developer_mode
                    ? x`
                      <div class="form-group">
                        ${_textInput({
                          label: "Selora Cloud URL",
                          value:
                            host._config.selora_connect_url ||
                            "https://connect.selorahomes.com",
                          oninput: (e5) =>
                            host._updateConfig(
                              "selora_connect_url",
                              e5.target.value,
                            ),
                        })}
                      </div>
                    `
                    : ""
                }
              `
              : isGemini
                ? x`
                  <div class="form-group">
                    <label>API Key</label>
                    ${
                      host._config.gemini_api_key_set
                        ? x`<button
                          class="key-hint key-set key-hint-btn"
                          title="Click to replace key"
                          @click=${() => {
                            host._showApiKeyInput = !host._showApiKeyInput;
                            if (!host._showApiKeyInput) host._newApiKey = "";
                            host.requestUpdate();
                          }}
                        >
                          <ha-icon
                            icon="mdi:check-circle"
                            style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                          ></ha-icon>
                          ${host._config.gemini_api_key_hint}
                          <ha-icon
                            icon="${host._showApiKeyInput ? "mdi:close" : "mdi:pencil"}"
                            class="key-hint-action"
                          ></ha-icon>
                        </button>`
                        : ""
                    }
                    ${
                      !host._config.gemini_api_key_set || host._showApiKeyInput
                        ? _textInput({
                            label: host._config.gemini_api_key_set
                              ? "Enter new key"
                              : "Enter API key",
                            type: "password",
                            value: host._newApiKey,
                            oninput: (e5) =>
                              (host._newApiKey = e5.target.value),
                            placeholder: "AIza...",
                            style: "margin-top:8px;",
                          })
                        : ""
                    }
                  </div>
                  <div class="form-group">
                    ${_textInput({
                      label: "Model",
                      value: host._config.gemini_model,
                      oninput: (e5) =>
                        host._updateConfig("gemini_model", e5.target.value),
                    })}
                  </div>
                `
                : isAnthropic
                  ? x`
                    <div class="form-group">
                      <label>API Key</label>
                      ${
                        host._config.anthropic_api_key_set
                          ? x`<button
                            class="key-hint key-set key-hint-btn"
                            title="Click to replace key"
                            @click=${() => {
                              host._showApiKeyInput = !host._showApiKeyInput;
                              if (!host._showApiKeyInput) host._newApiKey = "";
                              host.requestUpdate();
                            }}
                          >
                            <ha-icon
                              icon="mdi:check-circle"
                              style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                            ></ha-icon>
                            ${host._config.anthropic_api_key_hint}
                            <ha-icon
                              icon="${host._showApiKeyInput ? "mdi:close" : "mdi:pencil"}"
                              class="key-hint-action"
                            ></ha-icon>
                          </button>`
                          : ""
                      }
                      ${
                        !host._config.anthropic_api_key_set ||
                        host._showApiKeyInput
                          ? _textInput({
                              label: host._config.anthropic_api_key_set
                                ? "Enter new key"
                                : "Enter API key",
                              type: "password",
                              value: host._newApiKey,
                              oninput: (e5) =>
                                (host._newApiKey = e5.target.value),
                              placeholder: "sk-ant-...",
                              style: "margin-top:8px;",
                            })
                          : ""
                      }
                    </div>
                    <div class="form-group">
                      ${_textInput({
                        label: "Model",
                        value: host._config.anthropic_model,
                        oninput: (e5) =>
                          host._updateConfig(
                            "anthropic_model",
                            e5.target.value,
                          ),
                      })}
                    </div>
                  `
                  : isOpenAI
                    ? x`
                      <div class="form-group">
                        <label>API Key</label>
                        ${
                          host._config.openai_api_key_set
                            ? x`<button
                              class="key-hint key-set key-hint-btn"
                              title="Click to replace key"
                              @click=${() => {
                                host._showApiKeyInput = !host._showApiKeyInput;
                                if (!host._showApiKeyInput)
                                  host._newApiKey = "";
                                host.requestUpdate();
                              }}
                            >
                              <ha-icon
                                icon="mdi:check-circle"
                                style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                              ></ha-icon>
                              ${host._config.openai_api_key_hint}
                              <ha-icon
                                icon="${host._showApiKeyInput ? "mdi:close" : "mdi:pencil"}"
                                class="key-hint-action"
                              ></ha-icon>
                            </button>`
                            : ""
                        }
                        ${
                          !host._config.openai_api_key_set ||
                          host._showApiKeyInput
                            ? _textInput({
                                label: host._config.openai_api_key_set
                                  ? "Enter new key"
                                  : "Enter API key",
                                type: "password",
                                value: host._newApiKey,
                                oninput: (e5) =>
                                  (host._newApiKey = e5.target.value),
                                placeholder: "sk-...",
                                style: "margin-top:8px;",
                              })
                            : ""
                        }
                      </div>
                      <div class="form-group">
                        ${_textInput({
                          label: "Model",
                          value: host._config.openai_model,
                          oninput: (e5) =>
                            host._updateConfig("openai_model", e5.target.value),
                        })}
                      </div>
                    `
                    : isOpenRouter
                      ? x`
                        <div class="form-group">
                          <label>API Key</label>
                          ${
                            host._config.openrouter_api_key_set
                              ? x`<button
                                class="key-hint key-set key-hint-btn"
                                title="Click to replace key"
                                @click=${() => {
                                  host._showApiKeyInput =
                                    !host._showApiKeyInput;
                                  if (!host._showApiKeyInput)
                                    host._newApiKey = "";
                                  host.requestUpdate();
                                }}
                              >
                                <ha-icon
                                  icon="mdi:check-circle"
                                  style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                                ></ha-icon>
                                ${host._config.openrouter_api_key_hint}
                                <ha-icon
                                  icon="${host._showApiKeyInput ? "mdi:close" : "mdi:pencil"}"
                                  class="key-hint-action"
                                ></ha-icon>
                              </button>`
                              : ""
                          }
                          ${
                            !host._config.openrouter_api_key_set ||
                            host._showApiKeyInput
                              ? _textInput({
                                  label: host._config.openrouter_api_key_set
                                    ? "Enter new key"
                                    : "Enter API key",
                                  type: "password",
                                  value: host._newApiKey,
                                  oninput: (e5) =>
                                    (host._newApiKey = e5.target.value),
                                  placeholder: "sk-or-...",
                                  style: "margin-top:8px;",
                                })
                              : ""
                          }
                        </div>
                        <div class="form-group">
                          ${_textInput({
                            label: "Model",
                            value: host._config.openrouter_model,
                            oninput: (e5) =>
                              host._updateConfig(
                                "openrouter_model",
                                e5.target.value,
                              ),
                            placeholder: "anthropic/claude-sonnet-4.5",
                          })}
                        </div>
                      `
                      : isSeloraLocal
                        ? x`
                          <div class="form-group">
                            ${_textInput({
                              label: "Add-on Host",
                              value: host._config.selora_local_host,
                              oninput: (e5) =>
                                host._updateConfig(
                                  "selora_local_host",
                                  e5.target.value,
                                ),
                              placeholder: "http://localhost:5310",
                            })}
                          </div>
                          <p
                            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 8px;"
                          >
                            Selora AI picks the right specialist model
                            (commands, automations, answers, clarifications) per
                            request automatically.
                          </p>
                        `
                        : x`
                          <div class="form-group">
                            ${_textInput({
                              label: "Host",
                              value: host._config.ollama_host,
                              oninput: (e5) =>
                                host._updateConfig(
                                  "ollama_host",
                                  e5.target.value,
                                ),
                            })}
                          </div>
                          <div class="form-group">
                            ${_textInput({
                              label: "Model",
                              value: host._config.ollama_model,
                              oninput: (e5) =>
                                host._updateConfig(
                                  "ollama_model",
                                  e5.target.value,
                                ),
                            })}
                          </div>
                        `
          }
          ${
            isSeloraCloud && !host._config.aigateway_linked
              ? ""
              : x`
                <div class="card-save-bar">
                  <button
                    class="btn btn-primary"
                    @click=${host._saveLlmConfig}
                    ?disabled=${host._savingLlmConfig}
                  >
                    ${
                      host._savingLlmConfig
                        ? x`<span
                            class="spinner"
                            style="width:14px;height:14px;"
                          ></span>
                          Validating…`
                        : "Save"
                    }
                  </button>
                </div>
              `
          }
          ${
            host._llmSaveStatus
              ? x`<div
                class="save-feedback save-feedback--${host._llmSaveStatus.type}"
              >
                <ha-icon
                  icon="${host._llmSaveStatus.type === "success" ? "mdi:check-circle" : "mdi:alert-circle"}"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${host._llmSaveStatus.message}
              </div>`
              : ""
          }
        </div>

        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>MCP Server</h3>
          </div>
          <p class="section-card-subtitle">
            Expose your home to external AI tools like Openclaw, Claude Desktop,
            Cursor, or Windsurf.
          </p>

          <div class="settings-connect-block">
            <div
              class="service-row"
              style="border-bottom:none;padding-bottom:0;"
            >
              <div class="service-label-group">
                <label>Connect via Selora account</label>
                <span class="service-desc"
                  >Makes your MCP server reachable by external tools</span
                >
              </div>
              <ha-switch
                .checked=${host._config.selora_connect_enabled}
                @change=${(e5) => {
                  if (e5.target.checked) {
                    host._startOAuthLink();
                  } else {
                    host._unlinkConnect();
                  }
                }}
                ?disabled=${host._linkingConnect}
              ></ha-switch>
            </div>
            ${
              host._connectError
                ? x`<div
                  style="color:var(--error-color,#d32f2f);font-size:13px;padding:4px 0 0;"
                >
                  ${host._connectError}
                </div>`
                : ""
            }
            ${
              host._connectAuthorizeUrl
                ? x`<div
                  style="display:flex;flex-direction:column;gap:6px;padding:8px 0 0;"
                >
                  <a
                    class="btn btn-primary"
                    href=${host._connectAuthorizeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    style="align-self:flex-start;text-decoration:none;"
                  >
                    Open sign-in page →
                  </a>
                  <div
                    style="font-size:12px;color:var(--secondary-text-color);"
                  >
                    Opens in a new tab. After signing in, return to this page —
                    the panel updates automatically.
                  </div>
                </div>`
                : ""
            }
            ${
              host._config.selora_connect_enabled
                ? x`
                  <div
                    style="display:flex;align-items:center;gap:8px;padding:8px 0 0;"
                  >
                    <code
                      style="font-size:12px;word-break:break-all;flex:1;padding:8px 10px;background:var(--card-background-color);border-radius:6px;border:1px solid var(--divider-color);overflow:hidden;text-overflow:ellipsis;"
                      >${host._config.selora_mcp_url || `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`}</code
                    >
                    <ha-icon-button
                      @click=${() => {
                        const mcpUrl =
                          host._config.selora_mcp_url ||
                          `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`;
                        navigator.clipboard.writeText(mcpUrl);
                        host._showToast(
                          "MCP URL copied to clipboard",
                          "success",
                        );
                      }}
                    >
                      <ha-icon
                        icon="mdi:content-copy"
                        style="--mdc-icon-size:20px;"
                      ></ha-icon>
                    </ha-icon-button>
                  </div>
                `
                : ""
            }
            ${
              host._config.developer_mode &&
              !host._config.selora_connect_enabled
                ? x`
                  <div style="padding:8px 0 0;">
                    ${_textInput({
                      label: "Connect Server URL",
                      value:
                        host._config.selora_connect_url ||
                        "https://connect.selorahomes.com",
                      oninput: (e5) =>
                        host._updateConfig(
                          "selora_connect_url",
                          e5.target.value,
                        ),
                    })}
                  </div>
                `
                : ""
            }
          </div>

          <div class="settings-section-title">MCP TOKENS</div>
          <p
            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 8px;"
          >
            MCP tokens are an alternative to Selora Connect. Use them for tools
            that don't support OAuth or when you prefer token-based
            authentication.
          </p>
          ${
            host._mcpTokens.length === 0
              ? x`<div
                style="font-size:13px;color:var(--secondary-text-color);padding:4px 0 8px;"
              >
                No tokens yet.
              </div>`
              : x`
                <div class="mcp-token-list">
                  ${host._mcpTokens.map(
                    (t3) => x`
                      <div class="mcp-token-row">
                        <ha-icon
                          icon="mdi:key-variant"
                          style="--mdc-icon-size:20px;color:var(--selora-accent);flex-shrink:0;"
                        ></ha-icon>
                        <div class="mcp-token-info">
                          <div class="mcp-token-name">
                            ${t3.name}
                            <span
                              class="mcp-token-badge mcp-token-badge--${t3.permission_level}"
                              >${t3.permission_level.replace("_", " ")}</span
                            >
                          </div>
                          <div class="mcp-token-meta">
                            <span>${t3.token_prefix}${"*".repeat(8)}</span>
                            ${
                              t3.expires_at
                                ? x`<span
                                  >&middot; expires
                                  ${new Date(t3.expires_at).toLocaleDateString(
                                    void 0,
                                    { month: "short", day: "numeric" },
                                  )}</span
                                >`
                                : ""
                            }
                            ${
                              t3.last_used_at
                                ? x`<span
                                  >&middot; used
                                  ${_timeAgo(t3.last_used_at)}</span
                                >`
                                : ""
                            }
                          </div>
                        </div>
                        <ha-icon-button
                          ?disabled=${host._revokingTokenId === t3.id}
                          @click=${() => host._revokeMcpToken(t3.id)}
                        >
                          ${
                            host._revokingTokenId === t3.id
                              ? x`<span
                                class="spinner"
                                style="width:14px;height:14px;"
                              ></span>`
                              : x`<ha-icon
                                icon="mdi:delete-outline"
                                style="--mdc-icon-size:20px;"
                              ></ha-icon>`
                          }
                        </ha-icon-button>
                      </div>
                    `,
                  )}
                </div>
              `
          }
          <button
            class="btn btn-outline"
            style="margin-top:8px;"
            @click=${() => host._openCreateTokenDialog()}
          >
            <ha-icon icon="mdi:plus" style="--mdc-icon-size:16px;"></ha-icon>
            Add token
          </button>
        </div>

        ${renderCreateTokenDialog(host)}

        <details class="section-card settings-section advanced-section" open>
          <summary class="advanced-toggle">
            Advanced settings
            <ha-icon
              icon="mdi:chevron-right"
              class="advanced-chevron"
              style="margin-left:auto;"
            ></ha-icon>
          </summary>

          <div class="settings-section-title" style="margin-top:16px;">
            BACKGROUND SERVICES
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Data collector (AI analysis)</label>
                <span class="service-desc"
                  >Feeds entity history to Selora AI</span
                >
              </div>
              <ha-switch
                .checked=${host._config.collector_enabled}
                @change=${(e5) => host._updateConfig("collector_enabled", e5.target.checked)}
              ></ha-switch>
            </div>
            ${
              host._config.collector_enabled
                ? x`
                  <div class="service-details">
                    <div style="display:flex;gap:12px;">
                      <div class="form-group" style="flex:1;margin-bottom:0;">
                        <label>Mode</label>
                        <select
                          class="form-select"
                          .value=${host._config.collector_mode}
                          @change=${(e5) =>
                            host._updateConfig(
                              "collector_mode",
                              e5.target.value,
                            )}
                        >
                          <option value="continuous">Continuous</option>
                          <option value="scheduled">Scheduled Window</option>
                        </select>
                      </div>
                      <div
                        class="form-group"
                        style="width:130px;margin-bottom:0;"
                      >
                        <label>Interval (s)</label>
                        <input
                          class="form-select"
                          type="number"
                          .value=${host._config.collector_interval}
                          @input=${(e5) =>
                            host._updateConfig(
                              "collector_interval",
                              parseInt(e5.target.value),
                            )}
                          style="width:100%;box-sizing:border-box;"
                        />
                      </div>
                    </div>
                    ${
                      host._config.collector_mode === "scheduled"
                        ? x`
                          <div style="display:flex;gap:12px;margin-top:12px;">
                            <div style="flex:1;">
                              ${_textInput({
                                label: "Start (HH:MM)",
                                value: host._config.collector_start_time,
                                oninput: (e5) =>
                                  host._updateConfig(
                                    "collector_start_time",
                                    e5.target.value,
                                  ),
                              })}
                            </div>
                            <div style="flex:1;">
                              ${_textInput({
                                label: "End (HH:MM)",
                                value: host._config.collector_end_time,
                                oninput: (e5) =>
                                  host._updateConfig(
                                    "collector_end_time",
                                    e5.target.value,
                                  ),
                              })}
                            </div>
                          </div>
                        `
                        : ""
                    }
                  </div>
                `
                : ""
            }
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Network discovery</label>
                <span class="service-desc"
                  >Scans local network for new devices</span
                >
              </div>
              <ha-switch
                .checked=${host._config.discovery_enabled}
                @change=${(e5) => host._updateConfig("discovery_enabled", e5.target.checked)}
              ></ha-switch>
            </div>
            ${
              host._config.discovery_enabled
                ? x`
                  <div class="service-details">
                    <div style="display:flex;gap:12px;">
                      <div class="form-group" style="flex:1;margin-bottom:0;">
                        <label>Mode</label>
                        <select
                          class="form-select"
                          .value=${host._config.discovery_mode}
                          @change=${(e5) =>
                            host._updateConfig(
                              "discovery_mode",
                              e5.target.value,
                            )}
                        >
                          <option value="continuous">Continuous</option>
                          <option value="scheduled">Scheduled Window</option>
                        </select>
                      </div>
                      <div
                        class="form-group"
                        style="width:130px;margin-bottom:0;"
                      >
                        <label>Interval (s)</label>
                        <input
                          class="form-select"
                          type="number"
                          .value=${host._config.discovery_interval}
                          @input=${(e5) =>
                            host._updateConfig(
                              "discovery_interval",
                              parseInt(e5.target.value),
                            )}
                          style="width:100%;box-sizing:border-box;"
                        />
                      </div>
                    </div>
                    ${
                      host._config.discovery_mode === "scheduled"
                        ? x`
                          <div style="display:flex;gap:12px;margin-top:12px;">
                            <div style="flex:1;">
                              ${_textInput({
                                label: "Start (HH:MM)",
                                value: host._config.discovery_start_time,
                                oninput: (e5) =>
                                  host._updateConfig(
                                    "discovery_start_time",
                                    e5.target.value,
                                  ),
                              })}
                            </div>
                            <div style="flex:1;">
                              ${_textInput({
                                label: "End (HH:MM)",
                                value: host._config.discovery_end_time,
                                oninput: (e5) =>
                                  host._updateConfig(
                                    "discovery_end_time",
                                    e5.target.value,
                                  ),
                              })}
                            </div>
                          </div>
                        `
                        : ""
                    }
                  </div>
                `
                : ""
            }
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Pattern detection</label>
                <span class="service-desc"
                  >Detects recurring usage patterns and proposes
                  automations</span
                >
              </div>
              <ha-switch
                .checked=${host._config.pattern_detection_enabled !== false}
                @change=${(e5) =>
                  host._updateConfig(
                    "pattern_detection_enabled",
                    e5.target.checked,
                  )}
              ></ha-switch>
            </div>
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Auto-remove stale automations</label>
                <span class="service-desc"
                  >Deletes automations inactive for
                  ${host._config.stale_days || 5}+ days</span
                >
              </div>
              <ha-switch
                .checked=${host._config.auto_purge_stale || false}
                @change=${(e5) => host._updateConfig("auto_purge_stale", e5.target.checked)}
              ></ha-switch>
            </div>
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Developer mode</label>
                <span class="service-desc"
                  >Exposes raw entity payloads and debug logs</span
                >
              </div>
              <ha-switch
                .checked=${host._config.developer_mode}
                @change=${async (e5) => {
                  const val = e5.target.checked;
                  host._updateConfig("developer_mode", val);
                  try {
                    await host.hass.callWS({
                      type: "selora_ai/update_config",
                      config: { developer_mode: val },
                    });
                  } catch (err) {
                    host._showToast("Failed to save developer mode.", "error");
                  }
                }}
              ></ha-switch>
            </div>
          </div>

          <div class="card-save-bar">
            <button
              class="btn btn-primary"
              @click=${host._saveAdvancedConfig}
              ?disabled=${host._savingAdvancedConfig}
            >
              ${host._savingAdvancedConfig ? "Saving\u2026" : "Save"}
            </button>
          </div>
        </details>

        <div
          style="text-align:center;font-size:11px;opacity:0.35;margin-top:24px;"
        >
          <a
            href="https://github.com/SeloraHomes/ha-selora-ai/releases/tag/v${"0.9.0-dev"}"
            target="_blank"
            rel="noopener noreferrer"
            style="color:inherit;text-decoration:none;"
          >
            Selora AI v${"0.9.0-dev"}
          </a>
        </div>
      </div>
    </div>
  `;
}
var MCP_TOOLS = [
  { name: "selora_list_automations", label: "List automations", admin: false },
  { name: "selora_get_automation", label: "Get automation", admin: false },
  {
    name: "selora_validate_automation",
    label: "Validate automation",
    admin: false,
  },
  {
    name: "selora_create_automation",
    label: "Create automation",
    admin: true,
  },
  {
    name: "selora_accept_automation",
    label: "Accept automation",
    admin: true,
  },
  {
    name: "selora_delete_automation",
    label: "Delete automation",
    admin: true,
  },
  {
    name: "selora_get_home_snapshot",
    label: "Get home snapshot",
    admin: false,
  },
  { name: "selora_chat", label: "Chat", admin: true },
  { name: "selora_list_sessions", label: "List sessions", admin: false },
  { name: "selora_list_patterns", label: "List patterns", admin: false },
  { name: "selora_get_pattern", label: "Get pattern", admin: false },
  { name: "selora_list_suggestions", label: "List suggestions", admin: false },
  {
    name: "selora_accept_suggestion",
    label: "Accept suggestion",
    admin: true,
  },
  {
    name: "selora_dismiss_suggestion",
    label: "Dismiss suggestion",
    admin: true,
  },
  { name: "selora_trigger_scan", label: "Trigger scan", admin: true },
  { name: "selora_list_devices", label: "List devices", admin: false },
  { name: "selora_get_device", label: "Get device", admin: false },
];
function _timeAgo(isoString) {
  if (!isoString) return "never";
  const seconds = Math.floor(
    (Date.now() - new Date(isoString).getTime()) / 1e3,
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
function renderCreateTokenDialog(host) {
  if (!host._showCreateTokenDialog) return "";
  if (host._createdToken) {
    return x`
      <div class="modal-overlay" @click=${() => host._closeCreateTokenDialog()}>
        <div
          class="modal-content"
          style="max-width:480px;"
          @click=${(e5) => e5.stopPropagation()}
        >
          <h3 style="margin:0 0 12px;">Token Created</h3>
          <p
            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 12px;"
          >
            Copy this token now — it won't be shown again.
          </p>
          <div
            style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--card-background-color);border:1px solid var(--selora-accent);border-radius:8px;font-family:monospace;font-size:13px;word-break:break-all;"
          >
            <span style="flex:1;user-select:all;">${host._createdToken}</span>
            <button
              style="background:none;border:none;color:var(--selora-accent);cursor:pointer;padding:8px;border-radius:50%;flex-shrink:0;"
              @click=${() => {
                navigator.clipboard.writeText(host._createdToken);
                host._showToast("Token copied to clipboard", "success");
              }}
            >
              <ha-icon
                icon="mdi:content-copy"
                style="--mdc-icon-size:20px;"
              ></ha-icon>
            </button>
          </div>
          <div style="display:flex;justify-content:flex-end;margin-top:16px;">
            <button
              class="btn btn-primary"
              @click=${() => host._closeCreateTokenDialog()}
            >
              Done
            </button>
          </div>
        </div>
      </div>
    `;
  }
  const permission = host._newTokenPermission;
  return x`
    <div class="modal-overlay" @click=${() => host._closeCreateTokenDialog()}>
      <div
        class="modal-content"
        style="max-width:480px;"
        @click=${(e5) => e5.stopPropagation()}
      >
        <h3 style="margin:0 0 16px;">Create MCP Token</h3>

        <div class="form-group">
          <label>Name</label>
          <input
            class="modal-input"
            type="text"
            placeholder="e.g. Claude Desktop"
            .value=${host._newTokenName}
            @input=${(e5) => {
              host._newTokenName = e5.target.value;
            }}
            style="width:100%;box-sizing:border-box;"
          />
        </div>

        <div class="form-group">
          <label>Permission Level</label>
          <select
            class="form-select"
            .value=${permission}
            @change=${(e5) => {
              host._newTokenPermission = e5.target.value;
              host.requestUpdate();
            }}
          >
            <option value="read_only">Read Only</option>
            <option value="admin">Admin (all tools)</option>
            <option value="custom">Custom (select tools)</option>
          </select>
        </div>

        ${
          permission === "custom"
            ? x`
              <div class="form-group">
                <label>Allowed Tools</label>
                <div class="mcp-tool-checklist">
                  ${MCP_TOOLS.map(
                    (tool) => x`
                      <label class="mcp-tool-check">
                        <input
                          type="checkbox"
                          .checked=${host._newTokenTools[tool.name] || false}
                          @change=${(e5) => {
                            host._newTokenTools = {
                              ...host._newTokenTools,
                              [tool.name]: e5.target.checked,
                            };
                            host.requestUpdate();
                          }}
                        />
                        <span>${tool.label}</span>
                        ${
                          tool.admin
                            ? x`<span
                              class="mcp-token-badge mcp-token-badge--admin"
                              style="font-size:10px;padding:1px 5px;"
                              >admin</span
                            >`
                            : ""
                        }
                      </label>
                    `,
                  )}
                </div>
              </div>
            `
            : ""
        }

        <div class="form-group">
          <label>Expiration (optional)</label>
          <select
            class="form-select"
            .value=${host._newTokenExpiry}
            @change=${(e5) => {
              host._newTokenExpiry = e5.target.value;
              host.requestUpdate();
            }}
          >
            <option value="">Never expires</option>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
          </select>
        </div>

        <div
          style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;"
        >
          <button
            class="btn btn-outline"
            @click=${() => host._closeCreateTokenDialog()}
          >
            Cancel
          </button>
          <button
            class="btn btn-primary"
            ?disabled=${!host._newTokenName?.trim() || host._creatingToken}
            @click=${() => host._createMcpToken()}
          >
            ${
              host._creatingToken
                ? x`<span
                  class="spinner"
                  style="width:14px;height:14px;"
                ></span>`
                : "Create Token"
            }
          </button>
        </div>
      </div>
    </div>
  `;
}

// src/panel/render-usage.js
var _USAGE_KEYS = ["llm_tokens_in", "llm_tokens_out", "llm_calls", "llm_cost"];
var _USAGE_SENSOR_LABELS = {
  llm_tokens_in: "LLM Tokens In",
  llm_tokens_out: "LLM Tokens Out",
  llm_calls: "LLM Calls",
  llm_cost: "LLM Cost (estimate)",
};
function _findUsageSensors(hass) {
  const result = {};
  if (!hass?.states) return result;
  const entities = hass.entities || {};
  for (const [entityId, entry] of Object.entries(entities)) {
    if (entry?.platform !== "selora_ai") continue;
    if (!entityId.startsWith("sensor.")) continue;
    const uid = entry.unique_id || "";
    for (const key of _USAGE_KEYS) {
      if (uid.endsWith(key)) {
        const state = hass.states[entityId];
        if (state) result[key] = { entityId, state };
      }
    }
  }
  if (Object.keys(result).length === _USAGE_KEYS.length) return result;
  for (const [entityId, state] of Object.entries(hass.states)) {
    if (!entityId.startsWith("sensor.")) continue;
    const slug = entityId.slice(7);
    for (const key of _USAGE_KEYS) {
      if (
        !result[key] &&
        (slug === key || slug.endsWith(key) || slug.startsWith(key))
      ) {
        result[key] = { entityId, state };
      }
    }
  }
  return result;
}
function _fmtTokens(n5) {
  const v2 = Number(n5) || 0;
  if (v2 >= 1e6) return (v2 / 1e6).toFixed(2) + "M";
  if (v2 >= 1e3) return (v2 / 1e3).toFixed(1) + "k";
  return Math.round(v2).toLocaleString();
}
function _fmtUsd(n5) {
  const v2 = Number(n5) || 0;
  if (v2 === 0) return "$0.00";
  if (v2 < 0.01) return "<$0.01";
  return "$" + v2.toFixed(2);
}
function _fmtInt(n5) {
  return (Number(n5) || 0).toLocaleString();
}
async function _fetchPeriodStats(hass, statisticIds, periodStart) {
  if (!hass) return {};
  try {
    const result = await hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: periodStart.toISOString(),
      statistic_ids: statisticIds,
      period: "hour",
      types: ["change"],
    });
    return result || {};
  } catch (err) {
    console.warn("Selora AI: failed to fetch usage statistics", err);
    return {};
  }
}
function _sumChange(buckets) {
  if (!Array.isArray(buckets)) return 0;
  let total = 0;
  for (const b2 of buckets) {
    const v2 = Number(b2?.change ?? 0);
    if (Number.isFinite(v2)) total += v2;
  }
  return total;
}
async function loadUsageStats(host) {
  const sensors = _findUsageSensors(host.hass);
  const ids = _USAGE_KEYS.map((k2) => sensors[k2]?.entityId).filter(Boolean);
  const now = /* @__PURE__ */ new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfWeek.getDate() - 7);
  const startOfMonth = new Date(startOfToday);
  startOfMonth.setDate(1);
  const periodPromise =
    ids.length === 0
      ? Promise.resolve([{}, {}, {}])
      : Promise.all([
          _fetchPeriodStats(host.hass, ids, startOfToday),
          _fetchPeriodStats(host.hass, ids, startOfWeek),
          _fetchPeriodStats(host.hass, ids, startOfMonth),
        ]);
  const recentPromise = host.hass
    .callWS({ type: "selora_ai/usage/recent" })
    .then((r4) => (Array.isArray(r4?.events) ? r4.events : []))
    .catch((err) => {
      console.warn("Selora AI: failed to fetch recent usage events", err);
      return [];
    });
  const pricingPromise = host.hass
    .callWS({ type: "selora_ai/usage/pricing_defaults" })
    .then((r4) => r4?.pricing || {})
    .catch((err) => {
      console.warn("Selora AI: failed to fetch pricing defaults", err);
      return {};
    });
  const [periods, recent, pricingDefaults] = await Promise.all([
    periodPromise,
    recentPromise,
    pricingPromise,
  ]);
  const [today, week, month] = periods;
  const reduce = (raw) => {
    const out = {};
    for (const key of _USAGE_KEYS) {
      const entityId = sensors[key]?.entityId;
      out[key] = entityId ? _sumChange(raw[entityId]) : 0;
    }
    return out;
  };
  host._usageStats = {
    today: reduce(today),
    week: reduce(week),
    month: reduce(month),
  };
  host._usageRecent = recent;
  host._pricingDefaults = pricingDefaults;
  host.requestUpdate();
}
var _KIND_LABELS = {
  chat: "Chat",
  chat_tool_round: "Chat \u2014 tool calls",
  suggestions: "Suggestion engine",
  command: "One-shot commands",
  session_title: "Session titles",
  health_check: "Health checks",
  raw: "Other",
};
function _kindLabel(kind) {
  return _KIND_LABELS[kind] || kind;
}
var _INTENT_LABELS = {
  command: "command",
  automation: "automation",
  scene: "scene",
  delayed_command: "delayed command",
  cancel: "cancellation",
  clarification: "clarification",
  answer: "answer",
};
function _intentLabel(intent) {
  if (!intent) return "";
  return _INTENT_LABELS[intent] || intent;
}
function _groupByKind(events) {
  const groups = /* @__PURE__ */ new Map();
  for (const e5 of events) {
    const key = e5.kind || "raw";
    let g2 = groups.get(key);
    if (!g2) {
      g2 = {
        kind: key,
        calls: 0,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        intents: /* @__PURE__ */ new Map(),
      };
      groups.set(key, g2);
    }
    g2.calls += 1;
    g2.input_tokens += Number(e5.input_tokens) || 0;
    g2.output_tokens += Number(e5.output_tokens) || 0;
    g2.cost_usd += Number(e5.cost_usd) || 0;
    if (e5.intent) {
      g2.intents.set(e5.intent, (g2.intents.get(e5.intent) || 0) + 1);
    }
  }
  return [...groups.values()].sort(
    (a4, b2) =>
      b2.cost_usd - a4.cost_usd ||
      b2.input_tokens + b2.output_tokens - (a4.input_tokens + a4.output_tokens),
  );
}
function _formatRelativeTime(iso) {
  if (!iso) return "";
  const t3 = new Date(iso).getTime();
  if (Number.isNaN(t3)) return "";
  const now = Date.now();
  const sec = Math.max(1, Math.round((now - t3) / 1e3));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}
var _SNIPPET_LABELS = {
  llm_cost: "Cost",
  llm_tokens_in: "Tokens in",
  llm_tokens_out: "Tokens out",
  llm_calls: "Calls",
};
function _yamlForSensor(entityId, label) {
  return `type: statistics-graph
title: ${label} per day
entities:
  - ${entityId}
stat_types:
  - change
period: day
days_to_show: 30`;
}
function _highlightYaml(yamlStr) {
  return yamlStr.split("\n").map((line) => {
    const indent = line.match(/^(\s*)/)[1];
    const rest = line.slice(indent.length);
    const listMatch = rest.match(/^(- )(.*)$/);
    if (listMatch) {
      return x`<div class="yaml-line">${indent}<span class="yaml-dash">- </span><span class="yaml-val">${listMatch[2]}</span></div>`;
    }
    const kvMatch = rest.match(/^([\w_-]+)(:)(.*)$/);
    if (kvMatch) {
      const val = kvMatch[3].trim();
      return x`<div class="yaml-line">${indent}<span class="yaml-key">${kvMatch[1]}</span><span class="yaml-colon">:</span>${val ? x` <span class="yaml-val">${val}</span>` : ""}</div>`;
    }
    return x`<div class="yaml-line">${line}</div>`;
  });
}
function _renderDashboardSnippet(host, sensors) {
  const selected = host._dashboardSnippetKey || _USAGE_KEYS[0];
  const s6 = sensors[selected];
  const entityId = s6?.entityId || `sensor.${selected}`;
  const label =
    s6?.state?.attributes?.friendly_name || _USAGE_SENSOR_LABELS[selected];
  const yaml = _yamlForSensor(entityId, label);
  return x`
    <div class="usage-snippet-pills">
      ${_USAGE_KEYS.map(
        (key) => x`
          <button
            class="usage-snippet-pill ${key === selected ? "active" : ""}"
            @click=${() => {
              host._dashboardSnippetKey = key;
              host.requestUpdate();
            }}
          >
            ${_SNIPPET_LABELS[key]}
          </button>
        `,
      )}
    </div>
    <div class="usage-yaml-block" style="position: relative;">
      <code>${_highlightYaml(yaml)}</code>
      <button
        class="usage-copy-btn"
        @click=${(e5) => {
          const block = e5.currentTarget.closest(".usage-yaml-block");
          const codeEl = block?.querySelector("code");
          if (codeEl) {
            const range = document.createRange();
            range.selectNodeContents(codeEl);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
          }
          const ta = document.createElement("textarea");
          ta.value = yaml;
          ta.style.cssText =
            "position:fixed;left:-9999px;top:-9999px;opacity:0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          const btn = e5.currentTarget;
          btn.textContent = "Copied!";
          setTimeout(() => {
            btn.textContent = "Copy";
          }, 1500);
        }}
      >
        Copy
      </button>
    </div>
    <p class="usage-help" style="margin-top: 8px;">
      The visual card picker will also find these sensors after the Recorder's
      first hourly compilation.
    </p>
  `;
}
function _renderTile({ label, value, sub, icon }) {
  return x`
    <div class="usage-tile">
      <div class="usage-tile-head">
        ${icon ? x`<ha-icon icon=${icon} style="--mdc-icon-size:16px;"></ha-icon>` : ""}
        <span class="usage-tile-label">${label}</span>
      </div>
      <div class="usage-tile-value">${value}</div>
      ${sub ? x`<div class="usage-tile-sub">${sub}</div>` : ""}
    </div>
  `;
}
function _renderPeriodRow(title, stats) {
  if (!stats) {
    return x`
      <div class="usage-period-row usage-period-row--loading">
        <span class="usage-period-title">${title}</span>
        <span class="usage-period-loading">Loading…</span>
      </div>
    `;
  }
  const tokensIn = stats.llm_tokens_in || 0;
  const tokensOut = stats.llm_tokens_out || 0;
  const calls = stats.llm_calls || 0;
  const cost = stats.llm_cost || 0;
  const empty = !tokensIn && !tokensOut && !calls && !cost;
  return x`
    <div class="usage-period-row">
      <span class="usage-period-title">${title}</span>
      ${
        empty
          ? x`<span class="usage-period-empty">No activity</span>`
          : x`
            <span class="usage-period-cost">${_fmtUsd(cost)}</span>
            <span class="usage-period-tokens">
              ${_fmtTokens(tokensIn + tokensOut)} tokens · ${_fmtInt(calls)}
              calls
            </span>
          `
      }
    </div>
  `;
}
function _renderBreakdown(groups, totalCost) {
  if (!groups || groups.length === 0) return "";
  return x`
    <div class="usage-breakdown">
      ${groups.map((g2) => {
        const pct =
          totalCost > 0 ? Math.round((g2.cost_usd / totalCost) * 100) : 0;
        const tokens = g2.input_tokens + g2.output_tokens;
        const intentEntries = [...g2.intents.entries()].sort(
          (a4, b2) => b2[1] - a4[1],
        );
        return x`
          <div class="usage-breakdown-row">
            <div class="usage-breakdown-head">
              <span class="usage-breakdown-label">${_kindLabel(g2.kind)}</span>
              <span class="usage-breakdown-cost">${_fmtUsd(g2.cost_usd)}</span>
            </div>
            <div class="usage-breakdown-bar">
              <div
                class="usage-breakdown-bar-fill"
                style="width:${Math.max(2, pct)}%;"
              ></div>
            </div>
            <div class="usage-breakdown-meta">
              <span>${_fmtInt(g2.calls)} call${g2.calls === 1 ? "" : "s"}</span>
              <span>·</span>
              <span>${_fmtTokens(tokens)} tokens</span>
              ${totalCost > 0 ? x`<span>·</span> <span>${pct}% of cost</span>` : ""}
            </div>
            ${
              intentEntries.length > 0
                ? x`
                  <div class="usage-breakdown-intents">
                    ${intentEntries.map(
                      ([intent, count]) => x`
                        <span class="usage-intent-pill">
                          ${_intentLabel(intent)} · ${_fmtInt(count)}
                        </span>
                      `,
                    )}
                  </div>
                `
                : ""
            }
          </div>
        `;
      })}
    </div>
  `;
}
function _renderRecentList(events) {
  return x`
    <div class="usage-recent-list">
      ${events.map((e5) => {
        const intent = _intentLabel(e5.intent);
        return x`
          <div class="usage-recent-row">
            <div class="usage-recent-main">
              <span class="usage-recent-kind">${_kindLabel(e5.kind)}</span>
              ${intent ? x`<span class="usage-recent-intent">→ ${intent}</span>` : ""}
              <span class="usage-recent-time">
                ${_formatRelativeTime(e5.timestamp)}
              </span>
            </div>
            <div class="usage-recent-details">
              <span class="usage-recent-model">${e5.provider} · ${e5.model}</span>
              <span class="usage-recent-tokens">
                ${_fmtTokens((e5.input_tokens || 0) + (e5.output_tokens || 0))}
                tok
              </span>
              <span class="usage-recent-cost">${_fmtUsd(e5.cost_usd)}</span>
            </div>
          </div>
        `;
      })}
    </div>
  `;
}
function _activeProviderModel(host) {
  const cfg = host?._config || {};
  const provider = cfg.llm_provider || "anthropic";
  const modelKey =
    provider === "anthropic"
      ? "anthropic_model"
      : provider === "gemini"
        ? "gemini_model"
        : provider === "openai"
          ? "openai_model"
          : provider === "openrouter"
            ? "openrouter_model"
            : provider === "ollama"
              ? "ollama_model"
              : null;
  const model = modelKey ? cfg[modelKey] || "" : "";
  return { provider, model };
}
function _defaultPriceFor(host, provider, model) {
  const table = host?._pricingDefaults || {};
  return table[provider]?.[model] || null;
}
function _overridePriceFor(host, provider, model) {
  const overrides = host?._config?.llm_pricing_overrides || {};
  return overrides[provider]?.[model] || null;
}
function _formatPrice(n5) {
  const v2 = Number(n5);
  if (!Number.isFinite(v2)) return "\u2014";
  return "$" + v2.toFixed(v2 < 1 ? 3 : 2).replace(/\.?0+$/, "") + " / MTok";
}
async function _savePricingOverride(host, provider, model, inPrice, outPrice) {
  if (!host?._config) return;
  const current = { ...(host._config.llm_pricing_overrides || {}) };
  const perProvider = { ...(current[provider] || {}) };
  const inNum = Number(inPrice);
  const outNum = Number(outPrice);
  if (
    !Number.isFinite(inNum) ||
    inNum < 0 ||
    !Number.isFinite(outNum) ||
    outNum < 0
  ) {
    host._showToast?.("Pricing must be non-negative numbers.", "error");
    return;
  }
  perProvider[model] = [inNum, outNum];
  current[provider] = perProvider;
  try {
    await host.hass.callWS({
      type: "selora_ai/update_config",
      config: { llm_pricing_overrides: current },
    });
    host._config = { ...host._config, llm_pricing_overrides: current };
    host._pricingEdit = null;
    host._showToast?.("Pricing override saved.", "success");
    host.requestUpdate();
  } catch (err) {
    host._showToast?.("Failed to save pricing: " + err.message, "error");
  }
}
async function _clearPricingOverride(host, provider, model) {
  if (!host?._config) return;
  const current = { ...(host._config.llm_pricing_overrides || {}) };
  const perProvider = { ...(current[provider] || {}) };
  if (!(model in perProvider)) return;
  delete perProvider[model];
  if (Object.keys(perProvider).length === 0) {
    delete current[provider];
  } else {
    current[provider] = perProvider;
  }
  try {
    await host.hass.callWS({
      type: "selora_ai/update_config",
      config: { llm_pricing_overrides: current },
    });
    host._config = { ...host._config, llm_pricing_overrides: current };
    host._pricingEdit = null;
    host._showToast?.("Reset to default pricing.", "success");
    host.requestUpdate();
  } catch (err) {
    host._showToast?.("Failed to reset pricing: " + err.message, "error");
  }
}
function _renderPricingCard(host) {
  const { provider, model } = _activeProviderModel(host);
  if (provider === "ollama" || !model) {
    return x`
      <div class="section-card">
        <div class="section-card-header">
          <h3>Pricing</h3>
        </div>
        <p class="usage-help">
          ${provider === "ollama" ? "Ollama runs locally \u2014 no token costs to track." : "Configure an LLM provider and model in Settings to set custom pricing."}
        </p>
      </div>
    `;
  }
  const defaults = _defaultPriceFor(host, provider, model);
  const override = _overridePriceFor(host, provider, model);
  const editing =
    host._pricingEdit?.provider === provider &&
    host._pricingEdit?.model === model;
  const effective = override || defaults;
  return x`
    <div class="section-card">
      <div class="section-card-header">
        <h3>Pricing</h3>
        <span class="usage-section-sub">${provider} · ${model}</span>
      </div>
      <p class="usage-help" style="margin-top:0;">
        Cost estimates use these per-million-token rates. Anthropic defaults
        come from the
        <a
          href="https://platform.claude.com/docs/en/about-claude/pricing"
          target="_blank"
          rel="noopener noreferrer"
          >official pricing page</a
        >; override here if you have negotiated rates or are tracking a
        different model.
      </p>

      <div class="usage-pricing-row">
        <div class="usage-pricing-cell">
          <span class="usage-pricing-label">Input</span>
          <span class="usage-pricing-value">
            ${effective ? _formatPrice(effective[0]) : "\u2014"}
          </span>
          ${
            defaults
              ? x`<span class="usage-pricing-default">
                default ${_formatPrice(defaults[0])}
              </span>`
              : x`<span class="usage-pricing-default"
                >no built-in default</span
              >`
          }
        </div>
        <div class="usage-pricing-cell">
          <span class="usage-pricing-label">Output</span>
          <span class="usage-pricing-value">
            ${effective ? _formatPrice(effective[1]) : "\u2014"}
          </span>
          ${
            defaults
              ? x`<span class="usage-pricing-default">
                default ${_formatPrice(defaults[1])}
              </span>`
              : ""
          }
        </div>
      </div>

      ${
        editing
          ? x`
            <div class="usage-pricing-edit">
              <ha-textfield
                label="Input ($/MTok)"
                type="number"
                step="0.01"
                min="0"
                .value=${String(host._pricingEdit.input ?? "")}
                @input=${(e5) => {
                  host._pricingEdit = {
                    ...host._pricingEdit,
                    input: e5.target.value,
                  };
                }}
                style="flex:1;min-width:120px;"
              ></ha-textfield>
              <ha-textfield
                label="Output ($/MTok)"
                type="number"
                step="0.01"
                min="0"
                .value=${String(host._pricingEdit.output ?? "")}
                @input=${(e5) => {
                  host._pricingEdit = {
                    ...host._pricingEdit,
                    output: e5.target.value,
                  };
                }}
                style="flex:1;min-width:120px;"
              ></ha-textfield>
              <div class="usage-pricing-actions">
                <button
                  class="btn btn-outline"
                  @click=${() => {
                    host._pricingEdit = null;
                    host.requestUpdate();
                  }}
                >
                  Cancel
                </button>
                <button
                  class="btn btn-primary"
                  @click=${() =>
                    _savePricingOverride(
                      host,
                      provider,
                      model,
                      host._pricingEdit.input,
                      host._pricingEdit.output,
                    )}
                >
                  Save
                </button>
              </div>
            </div>
          `
          : x`
            <div class="usage-pricing-actions">
              <button
                class="btn btn-outline"
                @click=${() => {
                  host._pricingEdit = {
                    provider,
                    model,
                    input: effective ? effective[0] : "",
                    output: effective ? effective[1] : "",
                  };
                  host.requestUpdate();
                }}
              >
                <ha-icon
                  icon=${override ? "mdi:pencil" : "mdi:cash-edit"}
                  style="--mdc-icon-size:16px;"
                ></ha-icon>
                ${override ? "Edit override" : "Set custom pricing"}
              </button>
              ${
                override
                  ? x`
                    <button
                      class="btn btn-outline"
                      @click=${() => _clearPricingOverride(host, provider, model)}
                    >
                      Reset to default
                    </button>
                  `
                  : ""
              }
            </div>
          `
      }
    </div>
  `;
}
function renderUsage(host) {
  const sensors = _findUsageSensors(host.hass);
  const tokensIn = Number(sensors.llm_tokens_in?.state?.state) || 0;
  const tokensOut = Number(sensors.llm_tokens_out?.state?.state) || 0;
  const calls = Number(sensors.llm_calls?.state?.state) || 0;
  const cost = Number(sensors.llm_cost?.state?.state) || 0;
  const sensorsMissing = Object.keys(sensors).length === 0;
  const stats = host._usageStats || null;
  const recent = Array.isArray(host._usageRecent) ? host._usageRecent : null;
  const lastRecentEvent =
    recent && recent.length > 0 ? recent[recent.length - 1] : null;
  const lastProvider =
    sensors.llm_calls?.state?.attributes?.last_provider ||
    sensors.llm_cost?.state?.attributes?.last_provider ||
    lastRecentEvent?.provider ||
    null;
  const lastModel =
    sensors.llm_calls?.state?.attributes?.last_model ||
    sensors.llm_cost?.state?.attributes?.last_model ||
    lastRecentEvent?.model ||
    null;
  const breakdown = recent ? _groupByKind(recent) : null;
  const totalCost = breakdown
    ? breakdown.reduce((sum, g2) => sum + g2.cost_usd, 0)
    : 0;
  const bufTokensIn = breakdown
    ? breakdown.reduce((s6, g2) => s6 + g2.input_tokens, 0)
    : 0;
  const bufTokensOut = breakdown
    ? breakdown.reduce((s6, g2) => s6 + g2.output_tokens, 0)
    : 0;
  const bufCalls = breakdown
    ? breakdown.reduce((s6, g2) => s6 + g2.calls, 0)
    : 0;
  const dispTokensIn = sensorsMissing ? bufTokensIn : tokensIn;
  const dispTokensOut = sensorsMissing ? bufTokensOut : tokensOut;
  const dispCalls = sensorsMissing ? bufCalls : calls;
  const dispCost = sensorsMissing ? totalCost : cost;
  const hasTotals = dispTokensIn || dispTokensOut || dispCalls || dispCost;
  return x`
    <div class="scroll-view">
      <div class="usage-view">
        <a
          class="usage-crumb"
          href="#"
          @click=${(e5) => {
            e5.preventDefault();
            host._activeTab = "settings";
            host.requestUpdate();
          }}
        >
          <ha-icon icon="mdi:chevron-left"></ha-icon>
          <span>Back to settings</span>
        </a>
        <div class="usage-title-row">
          <h2>Token usage</h2>
          ${
            lastProvider
              ? x`
                <span class="usage-subtitle">
                  ${lastProvider}${lastModel ? ` \xB7 ${lastModel}` : ""}
                </span>
              `
              : ""
          }
        </div>

        ${
          sensorsMissing && recent !== null && recent.length === 0
            ? x`
              <div class="section-card usage-empty">
                <ha-icon
                  icon="mdi:information-outline"
                  style="--mdc-icon-size:20px;"
                ></ha-icon>
                <div>
                  <strong>No usage data yet.</strong>
                  <p>
                    Usage will appear after the first LLM call. Try chatting
                    with Selora AI or running a suggestion cycle. If you've
                    already used Selora AI and still see this, restart Home
                    Assistant so the new sensors get registered.
                  </p>
                </div>
              </div>
            `
            : x`
              ${
                hasTotals
                  ? x`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Totals</h3>
                      </div>
                      <div class="usage-tile-grid">
                        ${_renderTile({
                          label: "Cost",
                          value: _fmtUsd(dispCost),
                          sub: "USD estimate",
                          icon: "mdi:cash",
                        })}
                        ${_renderTile({
                          label: "Calls",
                          value: _fmtInt(dispCalls),
                          icon: "mdi:counter",
                        })}
                        ${_renderTile({
                          label: "Tokens in",
                          value: _fmtTokens(dispTokensIn),
                          icon: "mdi:upload",
                        })}
                        ${_renderTile({
                          label: "Tokens out",
                          value: _fmtTokens(dispTokensOut),
                          icon: "mdi:download",
                        })}
                      </div>
                    </div>
                  `
                  : ""
              }
              ${
                !sensorsMissing
                  ? x`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>By period</h3>
                      </div>
                      ${_renderPeriodRow("Today", stats?.today)}
                      ${_renderPeriodRow("Last 7 days", stats?.week)}
                      ${_renderPeriodRow("This month", stats?.month)}
                      <div class="usage-period-note">
                        Period buckets come from Home Assistant's long-term
                        statistics, which compile hourly. New activity may take
                        up to an hour to appear here.
                      </div>
                    </div>
                  `
                  : ""
              }

              <div class="section-card">
                <div class="section-card-header">
                  <h3>Where tokens go</h3>
                  <span class="usage-section-sub">
                    Last ${recent === null ? "\u2026" : recent.length}
                    call${recent && recent.length === 1 ? "" : "s"} · resets on
                    HA restart
                  </span>
                </div>
                ${
                  recent === null
                    ? x`<div class="usage-period-loading">Loading…</div>`
                    : recent.length === 0
                      ? x`<div class="usage-period-empty">
                        No calls recorded yet.
                      </div>`
                      : _renderBreakdown(breakdown, totalCost)
                }
              </div>

              ${
                recent && recent.length > 0
                  ? x`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Recent calls</h3>
                      </div>
                      ${_renderRecentList(recent.slice(-15).reverse())}
                    </div>
                  `
                  : ""
              }
              ${_renderPricingCard(host)}
              ${
                sensorsMissing
                  ? x`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Dashboard sensors</h3>
                      </div>
                      <p class="usage-help">
                        Restart Home Assistant to register the usage sensors.
                        Once registered, you can add them to any dashboard with
                        a
                        <code>statistics-graph</code> card.
                      </p>
                    </div>
                  `
                  : x`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Add to your dashboard</h3>
                      </div>
                      <p class="usage-help">
                        Each metric has a different scale — create one card per
                        sensor. Pick a metric, copy the YAML, then paste it in a
                        dashboard's YAML editor.
                      </p>
                      ${_renderDashboardSnippet(host, sensors)}
                    </div>
                  `
              }
            `
        }
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
  if (tab === "automations" || tab === "scenes" || tab === "settings") {
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
    this._deviceDetail = null;
    this._deviceDetailLoading = false;
    this._activeTab = "chat";
    if (this.narrow) this._showSidebar = false;
    await this.updateComplete;
    this._requestScrollChat({ force: true });
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
    this._deviceDetail = null;
    this._deviceDetailLoading = false;
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
    const textarea = this.shadowRoot?.querySelector(".composer-textarea");
    if (textarea) textarea.focus();
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
  _retryMessage: () => _retryMessage,
  _selectQuickAction: () => _selectQuickAction,
  _sendMessage: () => _sendMessage,
  _stopStreaming: () => _stopStreaming,
});
function _quickStart(message) {
  this._input = message;
  this._sendMessage();
}
function _selectQuickAction(action) {
  const text = action.value || action.label;
  for (const msg of this._messages) {
    if (msg.quick_actions && msg.quick_actions.includes(action)) {
      msg._qa_used = true;
      break;
    }
  }
  this._quickStart(text);
}
function _finaliseInterruption(host, assistantMsg, userMsg, reason) {
  if (!assistantMsg || assistantMsg._streaming === false) return;
  assistantMsg._streaming = false;
  assistantMsg._interrupted = true;
  assistantMsg._interruptReason = reason;
  assistantMsg._retryWith = userMsg;
  host._messages = [...host._messages];
  host._loading = false;
  host._streaming = false;
}
async function _sendMessage() {
  if (!this._input.trim() || this._loading) return;
  const userMsg = this._input;
  this._messages = [...this._messages, { role: "user", content: userMsg }];
  this._input = "";
  this._loading = true;
  const ta = this.shadowRoot?.querySelector(".composer-textarea");
  if (ta) ta.style.height = "auto";
  const assistantMsg = { role: "assistant", content: "", _streaming: true };
  this._messages = [...this._messages, assistantMsg];
  this._requestScrollChat({ force: true });
  const PRE_TOKEN_GRACE_MS = 12e4;
  const POST_TOKEN_GRACE_MS = 45e3;
  let firstTokenSeen = false;
  let lastActivityAt = Date.now();
  let watchdog = null;
  let onDisconnect = null;
  const teardown = () => {
    if (watchdog) {
      clearInterval(watchdog);
      watchdog = null;
    }
    if (onDisconnect) {
      this.hass.connection.removeEventListener("disconnected", onDisconnect);
      onDisconnect = null;
    }
    if (this._streamTeardown === teardown) {
      this._streamTeardown = null;
    }
  };
  this._streamTeardown = teardown;
  const cancelSubscription = () => {
    if (this._streamUnsub) {
      try {
        this._streamUnsub();
      } catch (_2) {}
      this._streamUnsub = null;
    }
  };
  try {
    const subscribePayload = {
      type: "selora_ai/chat_stream",
      message: userMsg,
    };
    if (this._activeSessionId) {
      subscribePayload.session_id = this._activeSessionId;
    }
    this._streaming = true;
    onDisconnect = () => {
      teardown();
      _finaliseInterruption(
        this,
        assistantMsg,
        userMsg,
        "Connection to Home Assistant was lost mid-response.",
      );
    };
    this.hass.connection.addEventListener("disconnected", onDisconnect);
    watchdog = setInterval(() => {
      if (!this._streaming) {
        teardown();
        return;
      }
      const grace = firstTokenSeen ? POST_TOKEN_GRACE_MS : PRE_TOKEN_GRACE_MS;
      if (Date.now() - lastActivityAt > grace) {
        teardown();
        cancelSubscription();
        _finaliseInterruption(
          this,
          assistantMsg,
          userMsg,
          firstTokenSeen
            ? "The server stopped responding."
            : "The server didn't reply in time.",
        );
      }
    }, 5e3);
    this._streamUnsub = await this.hass.connection.subscribeMessage((event) => {
      if (event.type === "token") {
        firstTokenSeen = true;
        lastActivityAt = Date.now();
        assistantMsg.content += event.text;
        this._messages = [...this._messages];
        this._loading = false;
        this._requestScrollChat();
      } else if (event.type === "heartbeat") {
        lastActivityAt = Date.now();
      } else if (event.type === "done") {
        teardown();
        const responseText = event.response || assistantMsg.content || "";
        const hasStructured =
          event.automation ||
          event.scene ||
          (event.executed && event.executed.length) ||
          (event.quick_actions && event.quick_actions.length);
        const trimmed = responseText.trim();
        const looksTruncated =
          !hasStructured &&
          trimmed.length > 0 &&
          trimmed.length < 400 &&
          (/[:,\-]\s*$/.test(trimmed) || // dangling colon / comma / bullet dash
            /\*\*[^*\n]*$/.test(trimmed) || // unterminated bold
            /^\s*-\s*$/.test(trimmed.split(/\n/).pop() || "") || // last line is just a bullet
            /\b(the|an?)\s*$/i.test(trimmed));
        if (looksTruncated) {
          cancelSubscription();
          assistantMsg.content = responseText;
          if (event.session_id) {
            if (event.session_id !== this._activeSessionId) {
              this._activeSessionId = event.session_id;
            }
            this._loadSessions();
          }
          _finaliseInterruption(
            this,
            assistantMsg,
            userMsg,
            "Response looks cut short \u2014 try again.",
          );
          return;
        }
        assistantMsg.content = responseText;
        assistantMsg.automation = event.automation || null;
        assistantMsg.automation_yaml = event.automation_yaml || null;
        assistantMsg.automation_status = event.automation ? "pending" : null;
        assistantMsg.automation_message_index =
          event.automation_message_index ?? null;
        assistantMsg.refining_automation_id =
          event.refining_automation_id || null;
        assistantMsg.scene = event.scene || null;
        assistantMsg.scene_yaml = event.scene_yaml || null;
        assistantMsg.scene_status = event.scene_status || null;
        assistantMsg.scene_message_index = event.scene_message_index ?? null;
        assistantMsg.refine_scene_id = event.refine_scene_id || null;
        assistantMsg.quick_actions = event.quick_actions || null;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        this._loading = false;
        this._streaming = false;
        this._streamUnsub = null;
        if (event.validation_error) {
          const label =
            event.validation_target === "scene" ? "Scene" : "Automation";
          this._showToast(
            `${label} validation failed: ${event.validation_error}`,
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
        teardown();
        cancelSubscription();
        _finaliseInterruption(
          this,
          assistantMsg,
          userMsg,
          event.message || "Couldn't reach the LLM provider.",
        );
      }
    }, subscribePayload);
  } catch (err) {
    teardown();
    cancelSubscription();
    _finaliseInterruption(
      this,
      assistantMsg,
      userMsg,
      err.message || "Couldn't start the chat session.",
    );
  }
}
function _retryMessage(text) {
  if (!text || this._loading) return;
  this._input = text;
  this._sendMessage();
}
function _stopStreaming() {
  if (this._streamTeardown) {
    this._streamTeardown();
  }
  if (this._streamUnsub) {
    this._streamUnsub();
    this._streamUnsub = null;
  }
  this._streaming = false;
  this._loading = false;
  const note = "\n\n_Cancelled by user_";
  const lastMsg = this._messages[this._messages.length - 1];
  if (lastMsg && lastMsg.role === "assistant") {
    lastMsg._streaming = false;
    if (!lastMsg.content?.endsWith(note)) {
      lastMsg.content = (lastMsg.content || "") + note;
    }
    this._messages = [...this._messages];
  } else {
    this._messages = [
      ...this._messages,
      { role: "assistant", content: note.trimStart() },
    ];
  }
}
function _requestScrollChat(opts) {
  if (opts && opts.force) this._scrollForce = true;
  if (this._scrollPending) return;
  this._scrollPending = true;
  requestAnimationFrame(() => {
    const force = !!this._scrollForce;
    this._scrollPending = false;
    this._scrollForce = false;
    const container = this.shadowRoot.getElementById("chat-messages");
    if (!container) return;
    if (!force) {
      const distance =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distance > 80) return;
    }
    container.scrollTop = container.scrollHeight;
  });
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
  _createdToast: () => _createdToast,
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
function _createdToast(alias, result) {
  if (result && result.risk_level === "elevated") {
    return {
      message: `Automation "${alias}" created (DISABLED) \u2014 uses elevated-risk actions (shell_command, python_script, webhook, etc.). Review carefully before enabling in Home Assistant.`,
      type: "warning",
    };
  }
  return {
    message: `Automation "${alias}" created \u2014 review and enable it from Home Assistant Automations.`,
    type: "info",
  };
}
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
    let createResult = null;
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
        createResult = await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation,
          session_id: this._activeSessionId,
        });
      }
    } else {
      createResult = await this.hass.callWS({
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
    if (refiningId && !createResult) {
      this._showToast(`Automation "${automation.alias}" updated.`, "success");
    } else {
      const toast = _createdToast(automation.alias, createResult);
      this._showToast(toast.message, toast.type);
    }
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
  this.shadowRoot.querySelector(".composer-textarea")?.focus();
}
async function _createAutomationFromSuggestion(automation) {
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/create_automation",
      automation,
    });
    await this._loadAutomations();
    const toast = _createdToast(automation.alias, result);
    this._showToast(toast.message, toast.type);
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
      let createResult = null;
      if (refiningId) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: edited,
          session_id: this._activeSessionId,
          version_message: "Refined via chat (with edits)",
        });
      } else {
        createResult = await this.hass.callWS({
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
      if (refiningId) {
        this._showToast(`Automation "${automation.alias}" updated.`, "success");
      } else {
        const toast = _createdToast(automation.alias, createResult);
        this._showToast(toast.message, toast.type);
      }
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
    let createResult;
    if (edited && edited !== originalYaml) {
      createResult = await this.hass.callWS({
        type: "selora_ai/apply_automation_yaml",
        yaml_text: edited,
      });
    } else {
      createResult = await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: auto,
      });
    }
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [yamlKey]: true,
    };
    await this._loadAutomations();
    const toast = _createdToast(auto.alias, createResult);
    this._showToast(toast.message, toast.type);
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
  _deleteAutomation: () => _deleteAutomation,
  _getSelectedAutomationIds: () => _getSelectedAutomationIds,
  _loadAutomationToChat: () => _loadAutomationToChat,
  _loadDiff: () => _loadDiff,
  _loadVersionHistory: () => _loadVersionHistory,
  _openDiffViewer: () => _openDiffViewer,
  _openVersionHistory: () => _openVersionHistory,
  _restoreVersion: () => _restoreVersion,
  _saveRenameAutomation: () => _saveRenameAutomation,
  _startRenameAutomation: () => _startRenameAutomation,
  _toggleAutomation: () => _toggleAutomation,
  _toggleAutomationSelection: () => _toggleAutomationSelection,
  _toggleBurgerMenu: () => _toggleBurgerMenu,
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
  if (automation.state === "unavailable") return false;
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
  if (!confirm(`Delete ${targets.length} selected automation(s)?`)) return;
  this._bulkActionInProgress = true;
  this._bulkActionLabel = `Deleting ${targets.length} automation(s)\u2026`;
  let successCount = 0;
  try {
    for (const auto of targets) {
      try {
        await this.hass.callWS({
          type: "selora_ai/delete_automation",
          automation_id: auto.automation_id,
        });
        successCount += 1;
      } catch (err) {
        console.error("Bulk delete failed", auto.automation_id, err);
      }
    }
    this._selectedAutomationIds = {};
    await this._loadAutomations();
    const failedCount = targets.length - successCount;
    if (failedCount === 0) {
      this._showToast(`Deleted ${successCount} automation(s).`, "success");
    } else {
      this._showToast(
        `Delete completed: ${successCount} succeeded, ${failedCount} failed.`,
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
async function _deleteAutomation(automationId) {
  if (!confirm("Delete this automation permanently?")) return;
  this._deletingAutomation = {
    ...this._deletingAutomation,
    [automationId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/delete_automation",
      automation_id: automationId,
    });
    await this._loadAutomations();
    this._showToast("Automation deleted.", "success");
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

// src/panel/scene-actions.js
var scene_actions_exports = {};
__export(scene_actions_exports, {
  _acceptScene: () => _acceptScene,
  _declineScene: () => _declineScene,
  _loadSceneToChat: () => _loadSceneToChat,
  _refineScene: () => _refineScene,
});
function _storedSceneIndex(msg, msgIndex) {
  return msg && msg.scene_message_index != null
    ? msg.scene_message_index
    : msgIndex;
}
async function _acceptScene(msgIndex) {
  const msg = this._messages[msgIndex] || {};
  const scene = msg.scene;
  if (!scene) return;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/accept_scene",
      session_id: this._activeSessionId,
      message_index: _storedSceneIndex(msg, msgIndex),
    });
    msg.scene_status = "saved";
    msg.scene_id = result.scene_id;
    msg.entity_id = result.entity_id;
    this._messages = [...this._messages];
    await this._loadScenes();
    this._showToast(`Scene "${scene.name}" created and saved.`, "success");
  } catch (err) {
    this._showToast("Failed to create scene: " + err.message, "error");
  }
}
async function _declineScene(msgIndex) {
  const msg = this._messages[msgIndex] || {};
  try {
    await this.hass.callWS({
      type: "selora_ai/set_scene_status",
      session_id: this._activeSessionId,
      message_index: _storedSceneIndex(msg, msgIndex),
      status: "declined",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to decline scene", err);
  }
}
async function _refineScene(msgIndex) {
  const msg = this._messages[msgIndex] || {};
  const scene = msg.scene;
  try {
    await this.hass.callWS({
      type: "selora_ai/set_scene_status",
      session_id: this._activeSessionId,
      message_index: _storedSceneIndex(msg, msgIndex),
      status: "refining",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to mark scene as refining", err);
  }
  const name = scene ? scene.name : "the scene";
  this._input = `Refine "${name}": `;
  this.shadowRoot.querySelector(".composer-textarea")?.focus();
}
async function _loadSceneToChat(sceneId) {
  if (!sceneId) return;
  this._loadingToChat = { ...this._loadingToChat, [sceneId]: true };
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/load_scene_to_session",
      scene_id: sceneId,
    });
    const sessionId = result?.session_id;
    if (sessionId) {
      this._activeSessionId = sessionId;
      this._input = "";
      this._activeTab = "chat";
      this._showSidebar = false;
      await this._openSession(sessionId);
    }
  } catch (err) {
    console.error("Failed to load scene to chat", err);
    this._showToast("Failed to load scene into chat: " + err.message, "error");
  } finally {
    this._loadingToChat = { ...this._loadingToChat, [sceneId]: false };
  }
  this.requestUpdate();
}

// src/panel.js
(() => {
  const PANEL_NAME = "selora-ai";
  const GUARD = "__seloraAiPanelMountGuard";
  if (window[GUARD]) return;
  window[GUARD] = true;
  const fix = (panelCustom) => {
    if (!panelCustom || panelCustom.tagName !== "HA-PANEL-CUSTOM") return;
    const cfg = panelCustom.panel?.config?._panel_custom;
    if (!cfg || cfg.name !== PANEL_NAME) return;
    if (panelCustom.querySelector(PANEL_NAME)) return;
    setTimeout(() => {
      if (!panelCustom.isConnected) return;
      if (panelCustom.querySelector(PANEL_NAME)) return;
      const el = document.createElement(PANEL_NAME);
      el.hass = panelCustom.hass;
      el.narrow = panelCustom.narrow;
      el.route = panelCustom.route;
      el.panel = panelCustom.panel;
      panelCustom.appendChild(el);
    }, 400);
  };
  let attempts = 0;
  const start = () => {
    const ha = document.querySelector("home-assistant");
    const main = ha?.shadowRoot?.querySelector("home-assistant-main");
    const resolver =
      main?.shadowRoot?.querySelector("partial-panel-resolver") ||
      main?.querySelector("partial-panel-resolver");
    if (!resolver) {
      if (++attempts < 30) setTimeout(start, 500);
      return;
    }
    for (const pc of resolver.querySelectorAll("ha-panel-custom")) fix(pc);
    new MutationObserver((muts) => {
      for (const m2 of muts) {
        for (const n5 of m2.addedNodes) if (n5.nodeType === 1) fix(n5);
      }
    }).observe(resolver, { childList: true });
  };
  start();
})();
var _SHA256_K = new Uint32Array([
  1116352408, 1899447441, 3049323471, 3921009573, 961987163, 1508970993,
  2453635748, 2870763221, 3624381080, 310598401, 607225278, 1426881987,
  1925078388, 2162078206, 2614888103, 3248222580, 3835390401, 4022224774,
  264347078, 604807628, 770255983, 1249150122, 1555081692, 1996064986,
  2554220882, 2821834349, 2952996808, 3210313671, 3336571891, 3584528711,
  113926993, 338241895, 666307205, 773529912, 1294757372, 1396182291,
  1695183700, 1986661051, 2177026350, 2456956037, 2730485921, 2820302411,
  3259730800, 3345764771, 3516065817, 3600352804, 4094571909, 275423344,
  430227734, 506948616, 659060556, 883997877, 958139571, 1322822218, 1537002063,
  1747873779, 1955562222, 2024104815, 2227730452, 2361852424, 2428436474,
  2756734187, 3204031479, 3329325298,
]);
function _sha256(msgBytes) {
  const rotr = (x2, n5) => (x2 >>> n5) | (x2 << (32 - n5));
  const len = msgBytes.length;
  const bitLen = len * 8;
  const blocks = Math.ceil((len + 9) / 64);
  const padded = new Uint8Array(blocks * 64);
  padded.set(msgBytes);
  padded[len] = 128;
  const dv = new DataView(padded.buffer);
  dv.setUint32(padded.length - 4, bitLen, false);
  let [h0, h1, h22, h3, h4, h5, h6, h7] = [
    1779033703, 3144134277, 1013904242, 2773480762, 1359893119, 2600822924,
    528734635, 1541459225,
  ];
  const w2 = new Uint32Array(64);
  for (let i5 = 0; i5 < padded.length; i5 += 64) {
    for (let t3 = 0; t3 < 16; t3++) w2[t3] = dv.getUint32(i5 + t3 * 4, false);
    for (let t3 = 16; t3 < 64; t3++) {
      const s0 =
        rotr(w2[t3 - 15], 7) ^ rotr(w2[t3 - 15], 18) ^ (w2[t3 - 15] >>> 3);
      const s1 =
        rotr(w2[t3 - 2], 17) ^ rotr(w2[t3 - 2], 19) ^ (w2[t3 - 2] >>> 10);
      w2[t3] = (w2[t3 - 16] + s0 + w2[t3 - 7] + s1) | 0;
    }
    let [a4, b2, c3, d3, e5, f2, g2, h8] = [h0, h1, h22, h3, h4, h5, h6, h7];
    for (let t3 = 0; t3 < 64; t3++) {
      const S1 = rotr(e5, 6) ^ rotr(e5, 11) ^ rotr(e5, 25);
      const ch = (e5 & f2) ^ (~e5 & g2);
      const t1 = (h8 + S1 + ch + _SHA256_K[t3] + w2[t3]) | 0;
      const S0 = rotr(a4, 2) ^ rotr(a4, 13) ^ rotr(a4, 22);
      const maj = (a4 & b2) ^ (a4 & c3) ^ (b2 & c3);
      const t22 = (S0 + maj) | 0;
      h8 = g2;
      g2 = f2;
      f2 = e5;
      e5 = (d3 + t1) | 0;
      d3 = c3;
      c3 = b2;
      b2 = a4;
      a4 = (t1 + t22) | 0;
    }
    h0 = (h0 + a4) | 0;
    h1 = (h1 + b2) | 0;
    h22 = (h22 + c3) | 0;
    h3 = (h3 + d3) | 0;
    h4 = (h4 + e5) | 0;
    h5 = (h5 + f2) | 0;
    h6 = (h6 + g2) | 0;
    h7 = (h7 + h8) | 0;
  }
  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  [h0, h1, h22, h3, h4, h5, h6, h7].forEach((v2, i5) =>
    ov.setUint32(i5 * 4, v2, false),
  );
  return out;
}
var SeloraAIPanel = class extends s4 {
  // HA's recent panel resolver wraps each panel in a scoped custom-element
  // registry (via @webcomponents/scoped-custom-element-registry). With the
  // default attachShadow options, our shadow root gets a fresh per-panel
  // registry that doesn't see globally-registered HA components, so
  // <ha-textfield>, <ha-switch>, etc. silently fail to upgrade — the
  // textfield renders as an empty unknown element (invisible) and the
  // switch falls back to undecorated mwc-switch (HA-default blue, ignoring
  // our --switch-checked-color overrides). Pass customElements explicitly
  // so attachShadow uses the global registry. Lit reads this static for
  // its default createRenderRoot, which keeps style adoption intact.
  static shadowRootOptions = {
    ...s4.shadowRootOptions,
    customElements: window.customElements,
  };
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
      _savingLlmConfig: { type: Boolean },
      _savingAdvancedConfig: { type: Boolean },
      _llmSaveStatus: { type: Object },
      _showApiKeyInput: { type: Boolean },
      _newApiKey: { type: String },
      // Usage tab (linked from Settings → LLM Provider)
      _usageStats: { type: Object },
      _usageRecent: { type: Array },
      _pricingDefaults: { type: Object },
      _pricingEdit: { type: Object },
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
      // Action loading states
      _deletingAutomation: { type: Object },
      _restoringVersion: { type: Object },
      _loadingToChat: { type: Object },
      // Bulk automation actions
      _selectedAutomationIds: { type: Object },
      _bulkActionInProgress: { type: Boolean },
      _bulkActionLabel: { type: String },
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
      // Unavailable automation modal
      _unavailableAutoId: { type: String },
      _unavailableAutoName: { type: String },
      // Stale automations modal
      _staleModalOpen: { type: Boolean },
      _staleSelected: { type: Object },
      _staleDetailAuto: { type: Object },
      _staleBulkDeleting: { type: Boolean },
      // Feedback modal
      _showFeedbackModal: { type: Boolean },
      _feedbackText: { type: String },
      _feedbackRating: { type: String },
      _feedbackCategory: { type: String },
      _feedbackEmail: { type: String },
      _submittingFeedback: { type: Boolean },
      // MCP tokens
      _mcpTokens: { type: Array },
      _showCreateTokenDialog: { type: Boolean },
      _newTokenName: { type: String },
      _newTokenPermission: { type: String },
      _newTokenTools: { type: Object },
      _newTokenExpiry: { type: String },
      _createdToken: { type: String },
      _creatingToken: { type: Boolean },
      _revokingTokenId: { type: String },
      // Device detail drawer
      _deviceDetail: { type: Object },
      _deviceDetailLoading: { type: Boolean },
      // Scenes tab
      _scenes: { type: Array },
      _sceneFilter: { type: String },
      _sceneSortBy: { type: String },
      _expandedScenes: { type: Object },
      _sceneYamlOpen: { type: Object },
      _openSceneBurger: { type: String },
      _deletingScene: { type: Object },
      _deleteSceneConfirmId: { type: String },
      _deleteSceneConfirmName: { type: String },
      // Theme
      _isDark: { type: Boolean },
      _primaryColor: { type: String },
      // Overflow menu
      _showOverflowMenu: { type: Boolean },
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
    this._savingLlmConfig = false;
    this._savingAdvancedConfig = false;
    this._llmSaveStatus = null;
    this._showApiKeyInput = false;
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
    this._deletingAutomation = {};
    this._restoringVersion = {};
    this._loadingToChat = {};
    this._selectedAutomationIds = {};
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this._toast = "";
    this._toastType = "info";
    this._toastTimer = null;
    this._expandedDetailId = null;
    this._showNewAutoDialog = false;
    this._newAutoName = "";
    this._suggestingName = false;
    this._unavailableAutoId = null;
    this._unavailableAutoName = null;
    this._staleModalOpen = false;
    this._staleSelected = {};
    this._staleDetailAuto = null;
    this._staleBulkDeleting = false;
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
    this._mcpTokens = [];
    this._showCreateTokenDialog = false;
    this._newTokenName = "";
    this._newTokenPermission = "read_only";
    this._newTokenTools = {};
    this._newTokenExpiry = "";
    this._createdToken = "";
    this._creatingToken = false;
    this._revokingTokenId = null;
    this._deviceDetail = null;
    this._deviceDetailLoading = false;
    this._scenes = [];
    this._sceneFilter = "";
    this._sceneSortBy = "recent";
    this._expandedScenes = {};
    this._sceneYamlOpen = {};
    this._openSceneBurger = null;
    this._deletingScene = {};
    this._deleteSceneConfirmId = null;
    this._deleteSceneConfirmName = null;
    this._quotaAlert = null;
    this._quotaUnsub = null;
    this._quotaSubPending = false;
    this._quotaClearTimer = null;
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
    this._loadScenes();
    this._loadConfig();
    this._loadMcpTokens();
    this._locationHandler = () => this._checkTabParam();
    window.addEventListener("location-changed", this._locationHandler);
    this._keyDownHandler = (e5) => {
      if (
        e5.key === "Escape" &&
        this._showFeedbackModal &&
        !this._submittingFeedback
      ) {
        this._closeFeedback();
        return;
      }
      if (
        e5.key === "Escape" &&
        this._activeTab === "chat" &&
        (this._streaming || this._loading)
      ) {
        e5.preventDefault();
        this._stopStreaming();
      }
    };
    window.addEventListener("keydown", this._keyDownHandler);
    this._closeOverflowHandler = () => {
      if (this._showOverflowMenu) this._showOverflowMenu = false;
    };
    document.addEventListener("click", this._closeOverflowHandler);
    this._keyboardOpen = false;
    this._resetHostKeyboardStyles = () => {
      const host = this.shadowRoot?.host;
      if (!host) return;
      host.style.height = "";
      host.style.position = "";
      host.style.top = "";
      host.style.left = "";
      host.style.right = "";
      this._keyboardOpen = false;
    };
    if (window.visualViewport) {
      this._viewportHandler = () => {
        if (!this.isConnected || document.hidden) return;
        const host = this.shadowRoot?.host;
        if (!host) return;
        const active = this.shadowRoot?.activeElement;
        const editing =
          active &&
          (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
        const vp = window.visualViewport;
        const keyboardHeight = window.innerHeight - vp.height;
        const isOpen = !!editing && keyboardHeight > 80;
        if (isOpen) {
          host.style.height = `${vp.height}px`;
          host.style.position = "fixed";
          host.style.top = `${vp.offsetTop}px`;
          host.style.left = "0";
          host.style.right = "0";
        } else {
          this._resetHostKeyboardStyles();
        }
        if (isOpen !== this._keyboardOpen) {
          this._keyboardOpen = isOpen;
          if (isOpen) {
            this._requestScrollChat();
          }
        }
      };
      window.visualViewport.addEventListener("resize", this._viewportHandler);
      window.visualViewport.addEventListener("scroll", this._viewportHandler);
    }
    this._visibilityHandler = () => {
      if (!document.hidden) this._resetHostKeyboardStyles();
    };
    document.addEventListener("visibilitychange", this._visibilityHandler);
    this._pageShowHandler = () => this._resetHostKeyboardStyles();
    window.addEventListener("pageshow", this._pageShowHandler);
    this._ensureQuotaSubscription();
    this._reconcileQuotaAlertOnReconnect();
    this._wsReadyHandler = () => this._handleWsReconnect();
    this._wsReadyConn = null;
    this._attachWsReadyListener();
  }
  _attachWsReadyListener() {
    const conn = this.hass?.connection;
    if (!conn || conn === this._wsReadyConn) return;
    if (this._wsReadyConn) {
      this._wsReadyConn.removeEventListener("ready", this._wsReadyHandler);
    }
    this._wsReadyConn = conn;
    conn.addEventListener("ready", this._wsReadyHandler);
  }
  _handleWsReconnect() {
    if (this._quotaUnsub) {
      try {
        this._quotaUnsub();
      } catch (_e) {}
      this._quotaUnsub = null;
    }
    this._quotaSubPending = false;
    this._ensureQuotaSubscription();
    this._loadSessions();
    this._loadAutomations();
    this._loadScenes();
    this._loadConfig();
    this._loadSuggestions();
  }
  _ensureQuotaSubscription() {
    if (this._quotaUnsub || this._quotaSubPending) return;
    if (!this.hass?.connection) return;
    this._quotaSubPending = true;
    this.hass.connection
      .subscribeEvents((evt) => {
        const data = evt?.data || {};
        const raw = Number(data.retry_after);
        const retryAfter = Number.isFinite(raw) && raw >= 0 ? raw : 60;
        this._setQuotaAlert({
          provider: data.provider || "unknown",
          model: data.model || "",
          retryAfter,
          message: data.message || "",
        });
      }, "selora_ai_quota_exceeded")
      .then((unsub) => {
        this._quotaUnsub = unsub;
        this._quotaSubPending = false;
        if (!this.isConnected) {
          try {
            unsub();
          } catch (_e) {}
          this._quotaUnsub = null;
        }
      })
      .catch((err) => {
        this._quotaSubPending = false;
        console.warn("Failed to subscribe to quota events", err);
      });
  }
  _setQuotaAlert(alert) {
    this._quotaAlert = {
      ...alert,
      until: Date.now() + alert.retryAfter * 1e3,
    };
    if (this._quotaClearTimer) clearTimeout(this._quotaClearTimer);
    this._quotaClearTimer = setTimeout(() => {
      this._dismissQuotaAlert();
    }, alert.retryAfter * 1e3);
    if (this._quotaTickTimer) clearInterval(this._quotaTickTimer);
    this._quotaTickTimer = setInterval(() => this.requestUpdate(), 1e3);
    this.requestUpdate();
  }
  _dismissQuotaAlert() {
    this._quotaAlert = null;
    if (this._quotaClearTimer) {
      clearTimeout(this._quotaClearTimer);
      this._quotaClearTimer = null;
    }
    if (this._quotaTickTimer) {
      clearInterval(this._quotaTickTimer);
      this._quotaTickTimer = null;
    }
    this.requestUpdate();
  }
  _reconcileQuotaAlertOnReconnect() {
    if (!this._quotaAlert) return;
    const remainingMs = this._quotaAlert.until - Date.now();
    if (remainingMs <= 0) {
      this._dismissQuotaAlert();
      return;
    }
    if (this._quotaClearTimer) clearTimeout(this._quotaClearTimer);
    this._quotaClearTimer = setTimeout(
      () => this._dismissQuotaAlert(),
      remainingMs,
    );
    if (this._quotaTickTimer) clearInterval(this._quotaTickTimer);
    this._quotaTickTimer = setInterval(() => this.requestUpdate(), 1e3);
    this.requestUpdate();
  }
  _quotaProviderLabel() {
    const p2 = this._quotaAlert?.provider;
    if (p2 === "selora_cloud") return "Selora Cloud";
    if (p2 === "anthropic") return "Anthropic";
    if (p2 === "openai") return "OpenAI";
    if (p2 === "openrouter") return "OpenRouter";
    if (p2 === "gemini") return "Gemini";
    if (p2 === "ollama") return "Ollama";
    return "your LLM provider";
  }
  _renderQuotaBanner() {
    if (!this._quotaAlert) return "";
    const remaining = Math.max(
      0,
      Math.ceil((this._quotaAlert.until - Date.now()) / 1e3),
    );
    return x`
      <div class="quota-banner" role="alert">
        <ha-icon icon="mdi:speedometer-slow"></ha-icon>
        <div class="quota-banner-text">
          <strong>${this._quotaProviderLabel()} quota reached.</strong>
          ${remaining > 0 ? x` Try again in ${remaining}s.` : " Retrying now\u2026"}
        </div>
        <button
          class="quota-banner-close"
          aria-label="Dismiss"
          @click=${() => this._dismissQuotaAlert()}
        >
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>
    `;
  }
  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._locationHandler) {
      window.removeEventListener("location-changed", this._locationHandler);
    }
    if (this._closeOverflowHandler) {
      document.removeEventListener("click", this._closeOverflowHandler);
    }
    if (this._keyDownHandler) {
      window.removeEventListener("keydown", this._keyDownHandler);
      this._keyDownHandler = null;
    }
    const vpHandler = this._viewportHandler;
    this._viewportHandler = null;
    if (vpHandler && window.visualViewport) {
      window.visualViewport.removeEventListener("resize", vpHandler);
      window.visualViewport.removeEventListener("scroll", vpHandler);
    }
    if (this._visibilityHandler) {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
      this._visibilityHandler = null;
    }
    if (this._pageShowHandler) {
      window.removeEventListener("pageshow", this._pageShowHandler);
      this._pageShowHandler = null;
    }
    if (this._resetHostKeyboardStyles) {
      this._resetHostKeyboardStyles();
      this._resetHostKeyboardStyles = null;
    }
    if (this._oauthPollTimer) {
      clearInterval(this._oauthPollTimer);
      this._oauthPollTimer = null;
    }
    if (this._aigatewayPollTimer) {
      clearInterval(this._aigatewayPollTimer);
      this._aigatewayPollTimer = null;
    }
    if (this._wsReadyConn && this._wsReadyHandler) {
      this._wsReadyConn.removeEventListener("ready", this._wsReadyHandler);
      this._wsReadyConn = null;
      this._wsReadyHandler = null;
    }
    if (this._quotaUnsub) {
      try {
        this._quotaUnsub();
      } catch (_e) {}
      this._quotaUnsub = null;
    }
    if (this._quotaClearTimer) {
      clearTimeout(this._quotaClearTimer);
      this._quotaClearTimer = null;
    }
    if (this._quotaTickTimer) {
      clearInterval(this._quotaTickTimer);
      this._quotaTickTimer = null;
    }
    if (this._streamUnsub) {
      try {
        this._stopStreaming();
      } catch (_e) {
        this._streamUnsub = null;
        this._streaming = false;
        this._loading = false;
        const lastMsg = this._messages[this._messages.length - 1];
        if (lastMsg && lastMsg._streaming) {
          lastMsg._streaming = false;
        }
      }
    }
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
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
  async _saveLlmConfig() {
    if (!this._config || this._savingLlmConfig) return;
    this._savingLlmConfig = true;
    this._llmSaveStatus = null;
    try {
      const provider = this._config.llm_provider;
      const newKey = this._newApiKey.trim();
      if (provider === "selora_cloud") {
        if (!this._config.aigateway_linked) return;
        const seloraPayload = { llm_provider: "selora_cloud" };
        if (this._config.selora_connect_url) {
          seloraPayload.selora_connect_url = this._config.selora_connect_url;
        }
        await this.hass.callWS({
          type: "selora_ai/update_config",
          config: seloraPayload,
        });
        await this._loadConfig();
        this._llmSaveStatus = {
          type: "success",
          message: "Switched to Selora Cloud.",
        };
        setTimeout(() => {
          this._llmSaveStatus = null;
          this.requestUpdate();
        }, 4e3);
        return;
      }
      const payload = { llm_provider: provider };
      if (provider === "anthropic") {
        payload.anthropic_model = this._config.anthropic_model;
        if (newKey) payload.anthropic_api_key = newKey;
      } else if (provider === "gemini") {
        payload.gemini_model = this._config.gemini_model;
        if (newKey) payload.gemini_api_key = newKey;
      } else if (provider === "openai") {
        payload.openai_model = this._config.openai_model;
        if (newKey) payload.openai_api_key = newKey;
      } else if (provider === "openrouter") {
        payload.openrouter_model = this._config.openrouter_model;
        if (newKey) payload.openrouter_api_key = newKey;
      } else {
        payload.ollama_host = this._config.ollama_host;
        payload.ollama_model = this._config.ollama_model;
      }
      const needsValidation = newKey || provider === "ollama";
      if (needsValidation) {
        const validatePayload = {
          type: "selora_ai/validate_llm_key",
          provider,
        };
        if (provider === "ollama") {
          validatePayload.host = this._config.ollama_host;
          validatePayload.model = this._config.ollama_model;
        } else {
          validatePayload.api_key = newKey;
          validatePayload.model = this._config[`${provider}_model`];
        }
        const result = await this.hass.callWS(validatePayload);
        if (!result.valid) {
          this._llmSaveStatus = {
            type: "error",
            message: result.error || "Invalid API key or provider unreachable.",
          };
          return;
        }
      }
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      this._newApiKey = "";
      this._showApiKeyInput = false;
      await this._loadConfig();
      this._llmSaveStatus = { type: "success", message: "LLM settings saved." };
      setTimeout(() => {
        this._llmSaveStatus = null;
        this.requestUpdate();
      }, 4e3);
    } catch (err) {
      this._llmSaveStatus = {
        type: "error",
        message: "Failed to save: " + err.message,
      };
    } finally {
      this._savingLlmConfig = false;
    }
  }
  async _saveAdvancedConfig() {
    if (!this._config || this._savingAdvancedConfig) return;
    this._savingAdvancedConfig = true;
    try {
      const payload = {
        collector_enabled: this._config.collector_enabled,
        collector_mode: this._config.collector_mode,
        collector_interval: this._config.collector_interval,
        collector_start_time: this._config.collector_start_time,
        collector_end_time: this._config.collector_end_time,
        discovery_enabled: this._config.discovery_enabled,
        discovery_mode: this._config.discovery_mode,
        discovery_interval: this._config.discovery_interval,
        discovery_start_time: this._config.discovery_start_time,
        discovery_end_time: this._config.discovery_end_time,
        pattern_detection_enabled:
          this._config.pattern_detection_enabled !== false,
        auto_purge_stale: this._config.auto_purge_stale || false,
        // Developer-only: Connect Server URL (editable when Connect is unlinked)
        selora_connect_url: this._config.selora_connect_url,
      };
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      await this._loadConfig();
      this._showToast("Advanced settings saved.", "success");
    } catch (err) {
      this._showToast("Failed to save: " + err.message, "error");
    } finally {
      this._savingAdvancedConfig = false;
    }
  }
  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig();
    this._loadMcpTokens();
  }
  get _llmNeedsSetup() {
    if (!this._config) return false;
    const provider = this._config.llm_provider;
    if (!provider) return true;
    if (provider === "anthropic") return !this._config.anthropic_api_key_set;
    if (provider === "gemini") return !this._config.gemini_api_key_set;
    if (provider === "openai") return !this._config.openai_api_key_set;
    if (provider === "openrouter") return !this._config.openrouter_api_key_set;
    if (provider === "selora_cloud") return !this._config.aigateway_linked;
    return false;
  }
  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }
  // ── OAuth PKCE helpers ──────────────────────────────────────────────
  _generateRandomString(length) {
    const chars =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
    const limit = 256 - (256 % chars.length);
    const result = [];
    while (result.length < length) {
      const arr = new Uint8Array(length - result.length);
      crypto.getRandomValues(arr);
      for (const b2 of arr) {
        if (b2 < limit && result.length < length) {
          result.push(chars[b2 % chars.length]);
        }
      }
    }
    return result.join("");
  }
  async _generateCodeChallenge(verifier) {
    const data = new TextEncoder().encode(verifier);
    let digest;
    if (typeof crypto !== "undefined" && crypto.subtle) {
      digest = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
    } else {
      digest = _sha256(data);
    }
    return btoa(String.fromCharCode(...digest))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }
  // ── OAuth Link flow ───────────────────────────────────────────────
  //
  // HA-mediated linking: PKCE state lives on the backend; we ask HA to
  // start a session and hand us an authorize URL. The user clicks a
  // real `<a target="_blank">` rendered with that URL — programmatic
  // clicks after `await` lose the user-gesture context and get blocked
  // in browsers and Companion app alike, so the URL has to be on a
  // genuinely-clicked anchor. Two clicks total: "Link" prepares the
  // URL, the resulting anchor opens the system browser. HA's callback
  // view finishes the exchange and fires `selora_ai_oauth_linked`.
  _OAUTH_LINK_TIMEOUT_MS = 10 * 60 * 1e3;
  _resetOAuthState(flow) {
    if (flow === "aigateway") {
      this._aigwAuthorizeUrl = "";
    } else {
      this._connectAuthorizeUrl = "";
    }
  }
  async _runOAuthLink({ flow, wsType, beforeStart, onSuccess, onError }) {
    let unsub = null;
    let timeout = null;
    const cleanup = () => {
      if (typeof unsub === "function") {
        try {
          unsub();
        } catch (_e) {}
        unsub = null;
      }
      if (timeout) {
        clearTimeout(timeout);
        timeout = null;
      }
      this._resetOAuthState(flow);
    };
    try {
      if (typeof beforeStart === "function") await beforeStart();
      unsub = await this.hass.connection.subscribeEvents((evt) => {
        const data = evt.data || {};
        if (data.flow !== flow) return;
        cleanup();
        if (data.ok) {
          onSuccess();
        } else {
          onError(data.error || "Linking failed.");
        }
      }, "selora_ai_oauth_linked");
      const result = await this.hass.callWS({
        type: wsType,
        connect_url: this._config?.selora_connect_url || "",
      });
      const authorizeUrl = result?.authorize_url;
      if (!authorizeUrl) throw new Error("No authorize URL returned.");
      if (flow === "aigateway") {
        this._aigwAuthorizeUrl = authorizeUrl;
      } else {
        this._connectAuthorizeUrl = authorizeUrl;
      }
      this.requestUpdate();
      timeout = setTimeout(() => {
        cleanup();
        onError(
          "Linking timed out. Please try again \u2014 make sure you finish signing in within 10 minutes.",
        );
      }, this._OAUTH_LINK_TIMEOUT_MS);
    } catch (err) {
      cleanup();
      onError(err.message || "Failed to start linking.");
    }
  }
  async _startOAuthLink() {
    if (this._linkingConnect) return;
    this._linkingConnect = true;
    this._connectError = "";
    this.requestUpdate();
    await this._runOAuthLink({
      flow: "connect",
      wsType: "selora_ai/start_connect_link",
      onSuccess: async () => {
        await this._loadConfig();
        this._linkingConnect = false;
        this._showToast("Selora Connect linked successfully.", "success");
        this.requestUpdate();
      },
      onError: (msg) => {
        this._connectError = msg;
        this._linkingConnect = false;
        this.requestUpdate();
      },
    });
  }
  async _unlinkConnect() {
    const ok = window.confirm(
      "Unlink Selora Connect?\n\nExternal MCP tools (Openclaw, Claude Desktop, Cursor, Windsurf) will lose access until you re-link.",
    );
    if (!ok) {
      this.requestUpdate();
      await this._loadConfig();
      return;
    }
    try {
      await this.hass.callWS({ type: "selora_ai/unlink_connect" });
      await this._loadConfig();
      this._showToast("Selora Connect unlinked.", "success");
    } catch (err) {
      this._showToast("Failed to unlink: " + err.message, "error");
    }
  }
  // ── AI Gateway OAuth Link flow ────────────────────────────────────
  async _startAIGatewayLink() {
    if (this._linkingAIGateway) return;
    this._linkingAIGateway = true;
    this._aigatewayError = "";
    this.requestUpdate();
    await this._runOAuthLink({
      flow: "aigateway",
      wsType: "selora_ai/start_aigw_link",
      beforeStart: async () => {
        if (this._config?.developer_mode && this._config?.selora_connect_url) {
          await this.hass.callWS({
            type: "selora_ai/update_config",
            config: { selora_connect_url: this._config.selora_connect_url },
          });
        }
      },
      onSuccess: async () => {
        await this._loadConfig();
        this._linkingAIGateway = false;
        this._showToast("Selora Cloud linked successfully.", "success");
        this.requestUpdate();
      },
      onError: (msg) => {
        this._aigatewayError = msg;
        this._linkingAIGateway = false;
        this.requestUpdate();
      },
    });
  }
  async _unlinkAIGateway() {
    const ok = window.confirm(
      "Unlink Selora Cloud?\n\nChat and automation suggestions will stop until you re-link your account in Settings.",
    );
    if (!ok) return;
    try {
      await this.hass.callWS({ type: "selora_ai/unlink_aigateway" });
      await this._loadConfig();
      this._showToast("Selora Cloud unlinked.", "success");
    } catch (err) {
      this._showToast("Failed to unlink: " + err.message, "error");
    }
  }
  // -------------------------------------------------------------------------
  // MCP Token Management
  // -------------------------------------------------------------------------
  async _loadMcpTokens() {
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/list_mcp_tokens",
      });
      this._mcpTokens = result.tokens || [];
    } catch (err) {
      console.error("Failed to load MCP tokens", err);
    }
  }
  async _createMcpToken() {
    if (this._creatingToken) return;
    this._creatingToken = true;
    this.requestUpdate();
    try {
      const payload = {
        type: "selora_ai/create_mcp_token",
        name: this._newTokenName,
        permission_level: this._newTokenPermission,
      };
      if (this._newTokenPermission === "custom") {
        payload.allowed_tools = Object.keys(this._newTokenTools).filter(
          (t3) => this._newTokenTools[t3],
        );
      }
      if (this._newTokenExpiry) {
        payload.expires_in_days = parseInt(this._newTokenExpiry, 10);
      }
      const result = await this.hass.callWS(payload);
      this._createdToken = result.token;
      await this._loadMcpTokens();
      this._showToast("MCP token created.", "success");
    } catch (err) {
      this._showToast("Failed to create token: " + err.message, "error");
      this._showCreateTokenDialog = false;
    } finally {
      this._creatingToken = false;
      this.requestUpdate();
    }
  }
  async _revokeMcpToken(tokenId) {
    this._revokingTokenId = tokenId;
    this.requestUpdate();
    try {
      await this.hass.callWS({
        type: "selora_ai/revoke_mcp_token",
        token_id: tokenId,
      });
      await this._loadMcpTokens();
      this._showToast("Token revoked.", "success");
    } catch (err) {
      this._showToast("Failed to revoke token: " + err.message, "error");
    } finally {
      this._revokingTokenId = null;
      this.requestUpdate();
    }
  }
  _openCreateTokenDialog() {
    this._newTokenName = "";
    this._newTokenPermission = "read_only";
    this._newTokenTools = {};
    this._newTokenExpiry = "";
    this._createdToken = "";
    this._showCreateTokenDialog = true;
    this.requestUpdate();
  }
  _closeCreateTokenDialog() {
    this._showCreateTokenDialog = false;
    this._createdToken = "";
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
    const duration = type === "warning" ? 8e3 : 3500;
    this._toastTimer = setTimeout(() => {
      this._toast = "";
      this._toastType = "info";
      this._toastTimer = null;
      this.requestUpdate();
    }, duration);
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
        integration_version: true ? "0.9.0-dev" : "unknown",
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
    if (this._config) {
      this.toggleAttribute("needs-setup", this._llmNeedsSetup);
    }
    this.toggleAttribute("quota-exceeded", !!this._quotaAlert);
    if (changedProps.has("hass")) {
      this._attachWsReadyListener();
      this._ensureQuotaSubscription();
      this._checkTabParam();
      const dark = this.hass?.themes?.darkMode;
      if (dark !== void 0) {
        this._isDark = dark;
        this.toggleAttribute("dark", dark);
      }
      const probe = document.createElement("div");
      probe.style.color = "var(--primary-color)";
      probe.style.display = "none";
      this.shadowRoot?.appendChild(probe);
      const resolved = getComputedStyle(probe).color;
      probe.remove();
      const m2 = resolved.match(/\d+/g);
      if (m2 && m2.length >= 3) {
        this._primaryColor =
          "#" +
          [m2[0], m2[1], m2[2]]
            .map((v2) => parseInt(v2, 10).toString(16).padStart(2, "0"))
            .join("");
      }
    }
    if (this.hass && this._pendingNewAutomation) {
      const name = this._pendingNewAutomation;
      this._pendingNewAutomation = null;
      this._newAutomationChat(name);
    }
    if (changedProps.has("_messages") && this._activeTab === "chat") {
      this._requestScrollChat();
    }
    if (
      this.hass &&
      (changedProps.has("_messages") ||
        changedProps.has("hass") ||
        (changedProps.has("_activeTab") && this._activeTab === "chat"))
    ) {
      this._hydrateEntityChips();
    }
    if (
      this._activeTab === "chat" &&
      (changedProps.has("_activeTab") || changedProps.has("_activeSessionId"))
    ) {
      this._focusComposerSoon();
    }
  }
  _focusComposerSoon() {
    requestAnimationFrame(() => {
      const ta = this.shadowRoot?.querySelector(".composer-textarea");
      if (!ta) return;
      const active = this.shadowRoot.activeElement;
      if (
        active &&
        active !== ta &&
        (active.tagName === "INPUT" || active.tagName === "TEXTAREA")
      ) {
        return;
      }
      ta.focus();
    });
  }
  // Hydrate `[[entity:<id>|…]]` and `[[entities:id1,id2,…]]` placeholders
  // with real HA tile cards. We try two construction paths so the
  // panel works across HA frontend variants:
  //   1. `window.loadCardHelpers().createCardElement({type:"tile",…})`
  //      — the documented API; also lazy-loads the card chunk.
  //   2. `document.createElement("hui-tile-card") + setConfig(…)` —
  //      direct construction once Lovelace has registered the
  //      element. We wait briefly via `customElements.whenDefined`
  //      so we don't race the registration.
  // Cards self-update when we keep their `.hass` property current, so
  // we just refresh that on every pass.
  async _hydrateEntityChips() {
    const root = this.shadowRoot;
    if (!root) return;
    const grids = root.querySelectorAll(".selora-entity-grid");
    if (grids.length === 0) return;
    const createTile = await this._getTileCardCreator();
    const registries = await this._ensureFullRegistries();
    let cardsAppended = false;
    for (const grid of grids) {
      const wired = grid.dataset.wired === "true";
      if (!wired) {
        const ids = (grid.dataset.entityIds || "")
          .split(",")
          .map((s6) => s6.trim())
          .filter(Boolean);
        let appended = 0;
        if (createTile) {
          const groups = /* @__PURE__ */ new Map();
          for (const id of ids) {
            if (!this.hass.states?.[id]) continue;
            const reg = registries.entities?.[id];
            const dev = reg?.device_id
              ? registries.devices?.[reg.device_id]
              : null;
            const areaId = reg?.area_id || dev?.area_id || null;
            const areaName = areaId
              ? registries.areas?.[areaId]?.name || null
              : null;
            if (!groups.has(areaName)) groups.set(areaName, []);
            groups.get(areaName).push(id);
          }
          const sortedGroups = [...groups.entries()].sort((a4, b2) => {
            if (!a4[0]) return 1;
            if (!b2[0]) return -1;
            return a4[0].localeCompare(b2[0]);
          });
          const showHeaders = groups.size > 1;
          const buildTile = (id) => {
            const card = createTile(id);
            if (!card) return null;
            card.hass = this.hass;
            const reg = registries.entities?.[id];
            const dev = reg?.device_id
              ? registries.devices?.[reg.device_id]
              : null;
            if (dev) {
              const parts = [];
              if (dev.manufacturer) parts.push(dev.manufacturer);
              if (dev.model) parts.push(dev.model);
              if (parts.length) card.title = parts.join(" \xB7 ");
            }
            return card;
          };
          const areaIdByName = /* @__PURE__ */ new Map();
          for (const a4 of Object.values(registries.areas || {})) {
            if (a4.name) areaIdByName.set(a4.name, a4.area_id);
          }
          for (const [areaName, areaIds] of sortedGroups) {
            if (showHeaders) {
              const header = document.createElement("div");
              header.className = "selora-area-header";
              const icon = document.createElement("ha-icon");
              icon.icon = areaName
                ? registries.areas?.[areaIdByName.get(areaName)]?.icon ||
                  "mdi:floor-plan"
                : "mdi:help-circle-outline";
              icon.className = "selora-area-icon";
              const label = document.createElement("span");
              label.textContent = areaName || "Unassigned";
              header.append(icon, label);
              grid.appendChild(header);
            }
            for (const id of areaIds) {
              try {
                const card = buildTile(id);
                if (!card) continue;
                grid.appendChild(card);
                appended += 1;
              } catch (e5) {
                console.warn("Selora: tile card create failed for", id, e5);
              }
            }
          }
        }
        if (appended === 0) {
          grid.textContent = ids.join(", ");
        } else {
          cardsAppended = true;
        }
        grid.dataset.wired = "true";
      }
      for (const card of grid.children) {
        if (card.hass !== void 0) {
          card.hass = { ...this.hass };
        }
      }
    }
    if (cardsAppended) {
      this._requestScrollChat({ force: true });
    }
  }
  // Lazily fetch the full entity + device registries via WS. The
  // `hass.entities` object exposed to panels is the *display* registry
  // (no device_id), so we can't get from entity_id to manufacturer
  // through it. Cached on `this` for the panel's lifetime — registry
  // changes mid-session won't refresh until the panel reloads, which
  // is fine for a tooltip.
  async _ensureFullRegistries() {
    if (this._fullRegistriesPromise) {
      const cached = await this._fullRegistriesPromise;
      const populated =
        Object.keys(cached.entities).length > 0 ||
        Object.keys(cached.devices).length > 0 ||
        Object.keys(cached.areas).length > 0;
      if (populated) return cached;
      this._fullRegistriesPromise = null;
    }
    this._fullRegistriesPromise = (async () => {
      try {
        const [entityList, deviceList, areaList] = await Promise.all([
          this.hass.callWS({ type: "config/entity_registry/list" }),
          this.hass.callWS({ type: "config/device_registry/list" }),
          this.hass.callWS({ type: "config/area_registry/list" }),
        ]);
        const entities = {};
        for (const e5 of entityList) entities[e5.entity_id] = e5;
        const devices = {};
        for (const d3 of deviceList) devices[d3.id] = d3;
        const areas = {};
        for (const a4 of areaList) areas[a4.area_id] = a4;
        return { entities, devices, areas };
      } catch (e5) {
        console.warn("Selora: registry list failed", e5);
        return { entities: {}, devices: {}, areas: {} };
      }
    })();
    return this._fullRegistriesPromise;
  }
  // Lazily resolve a single function `(entityId) => HTMLElement` that
  // builds an HA card for one entity. Uses the `entities` card type —
  // it renders the domain-appropriate native control inline (toggle
  // for switches, slider for volume, climate readout for HVAC, cover
  // arrows for blinds, etc.) instead of the bare tap-target shown by
  // `tile`. Tries `window.loadCardHelpers` first, then falls back to
  // `document.createElement("hui-entities-card")` once Lovelace has
  // registered the element. Cached on `this` so the chunk-load only
  // happens once per panel lifetime.
  async _getTileCardCreator() {
    if (this._tileCardCreator !== void 0) return this._tileCardCreator;
    const featuresForDomain = (entityId) => {
      const domain = entityId.split(".")[0];
      switch (domain) {
        case "light":
          return [{ type: "light-brightness" }];
        case "cover":
          return [{ type: "cover-open-close" }];
        case "fan":
          return [{ type: "fan-speed" }];
        case "media_player":
          return [{ type: "media-player-volume-slider" }];
        case "climate":
          return [{ type: "target-temperature" }];
        case "vacuum":
          return [{ type: "vacuum-commands" }];
        case "lock":
          return [{ type: "lock-commands" }];
        case "alarm_control_panel":
          return [{ type: "alarm-modes" }];
        case "water_heater":
          return [{ type: "water-heater-operation-modes" }];
        case "humidifier":
          return [{ type: "humidifier-toggle" }];
        case "lawn_mower":
          return [{ type: "lawn-mower-commands" }];
        default:
          return [];
      }
    };
    const buildConfig = (id) => ({
      type: "tile",
      entity: id,
      features: featuresForDomain(id),
    });
    if (typeof window.loadCardHelpers === "function") {
      try {
        const helpers = await window.loadCardHelpers();
        if (helpers && typeof helpers.createCardElement === "function") {
          this._tileCardCreator = (id) =>
            helpers.createCardElement(buildConfig(id));
          return this._tileCardCreator;
        }
      } catch (e5) {
        console.warn("Selora: loadCardHelpers() failed", e5);
      }
    }
    try {
      const ready = await Promise.race([
        customElements.whenDefined("hui-tile-card").then(() => true),
        new Promise((resolve) => setTimeout(() => resolve(false), 3e3)),
      ]);
      if (ready) {
        this._tileCardCreator = (id) => {
          const el = document.createElement("hui-tile-card");
          el.setConfig(buildConfig(id));
          return el;
        };
        return this._tileCardCreator;
      }
    } catch (e5) {
      console.warn("Selora: hui-tile-card whenDefined failed", e5);
    }
    this._tileCardCreator = null;
    return null;
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
  _renderSceneCard(msg, msgIndex) {
    return renderSceneCard(this, msg, msgIndex);
  }
  async _activateScene(sceneId, sceneName) {
    if (!sceneId) return;
    try {
      await this.hass.callService("scene", "turn_on", {
        entity_id: `scene.${sceneId}`,
      });
      this._showToast(`Scene "${sceneName || sceneId}" activated.`, "success");
    } catch (err) {
      this._showToast("Failed to activate scene: " + err.message, "error");
    }
  }
  _renderScenes() {
    return renderScenes(this);
  }
  async _loadScenes() {
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_scenes",
      });
      this._scenes = result?.scenes || [];
    } catch (err) {
      console.error("Failed to load scenes", err);
      this._scenes = [];
    }
  }
  async _refineSceneInChat(scene) {
    if (!scene) return;
    const sessionId = scene.session_id;
    const known = sessionId
      ? this._sessions.find((s6) => s6.id === sessionId)
      : null;
    try {
      if (known) {
        await this._openSession(sessionId);
      } else {
        await this._newSession();
      }
    } catch (err) {
      console.error("Failed to switch session for scene refine", err);
    }
    const ctx =
      known || scene.source !== "selora"
        ? ""
        : ` (scene_id: ${scene.scene_id})`;
    this._input = `Refine "${scene.name}"${ctx}: `;
    this._activeTab = "chat";
    this.requestUpdate();
    await this.updateComplete;
    const textarea = this.shadowRoot?.querySelector(".composer-textarea");
    if (textarea) textarea.focus();
  }
  async _confirmDeleteScene() {
    const sceneId = this._deleteSceneConfirmId;
    const name = this._deleteSceneConfirmName;
    if (!sceneId) return;
    this._deleteSceneConfirmId = null;
    this._deleteSceneConfirmName = null;
    this._deletingScene = { ...this._deletingScene, [sceneId]: true };
    try {
      await this.hass.callWS({
        type: "selora_ai/delete_scene",
        scene_id: sceneId,
      });
      this._showToast(`Scene "${name || sceneId}" deleted.`, "success");
      await this._loadScenes();
    } catch (err) {
      this._showToast("Failed to delete scene: " + err.message, "error");
    } finally {
      this._deletingScene = { ...this._deletingScene, [sceneId]: false };
    }
  }
  async _newSceneChat() {
    try {
      const { session_id } = await this.hass.callWS({
        type: "selora_ai/new_session",
      });
      this._activeSessionId = session_id;
      this._messages = [];
      this._input = "Create a scene that ";
      this._activeTab = "chat";
      this._welcomeKey = (this._welcomeKey || 0) + 1;
      await this._loadSessions();
      if (this.narrow) this._showSidebar = false;
      this.requestUpdate();
      await this.updateComplete;
      const textarea = this.shadowRoot?.querySelector(".composer-textarea");
      if (textarea) {
        textarea.focus();
        const len = this._input.length;
        textarea.setSelectionRange(len, len);
      }
    } catch (err) {
      console.error("Failed to start new scene chat", err);
      this._showToast("Failed to start new chat: " + err.message, "error");
    }
  }
  async _openDeviceDetail(deviceId) {
    if (!deviceId || !this.hass) return;
    this._deviceDetail = { name: "Loading..." };
    this._deviceDetailLoading = true;
    try {
      const result = await this.hass.connection.sendMessagePromise({
        type: "selora_ai/get_device_detail",
        device_id: deviceId,
      });
      this._deviceDetail = result;
    } catch (err) {
      this._deviceDetail = { name: "Error loading device", error: err.message };
    }
    this._deviceDetailLoading = false;
    await this.updateComplete;
    const detail = this.shadowRoot?.querySelector(".device-detail-drawer");
    if (detail) detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
  _renderUsage() {
    return renderUsage(this);
  }
  async _loadUsageStats() {
    await loadUsageStats(this);
  }
  _renderVersionHistoryDrawer(a4) {
    return renderVersionHistoryDrawer(this, a4);
  }
  _renderDiffViewer() {
    return renderDiffViewer(this);
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
        <div class="header-toolbar">
          ${
            this.narrow
              ? x`<button
                class="menu-btn"
                @click=${() =>
                  this.dispatchEvent(
                    new Event("hass-toggle-menu", {
                      bubbles: true,
                      composed: true,
                    }),
                  )}
              >
                <ha-icon icon="mdi:menu"></ha-icon>
              </button>`
              : ""
          }
          <img
            src="/api/selora_ai/${this._isDark ? "logo" : "logo-light"}.png"
            alt=""
            class="header-logo"
            @click=${() => {
              this._activeTab = "chat";
            }}
            style="cursor:pointer;"
          />
          <span
            class="header-title ${this._isDark ? "gold-text" : ""}"
            @click=${() => {
              this._activeTab = "chat";
            }}
            style="cursor:pointer;"
            >Selora AI</span
          >
          <div class="tabs-center">
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
                >Conversations</span
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
                >Automations</span
              >
            </div>
            <div
              class="tab ${this._activeTab === "scenes" ? "active" : ""}"
              @click=${() => {
                this._activeTab = "scenes";
                this._showSidebar = false;
                this._loadScenes();
              }}
            >
              <span class="tab-inner"
                ><ha-icon icon="mdi:palette-outline" class="tab-icon"></ha-icon
                >Scenes</span
              >
            </div>
          </div>
          <span class="header-spacer"></span>
          ${
            this._activeTab !== "chat" || this._messages.length > 0
              ? x`<button
                class="header-new-chat"
                title="New chat"
                aria-label="New chat"
                @click=${() => {
                  this._showOverflowMenu = false;
                  if (this._messages.length === 0) {
                    this._activeTab = "chat";
                    if (this.narrow) this._showSidebar = false;
                  } else {
                    this._newSession();
                  }
                }}
              >
                <ha-icon icon="mdi:square-edit-outline"></ha-icon>
                <span class="header-new-chat-label">New chat</span>
              </button>`
              : ""
          }
          <div class="overflow-btn-wrap">
            <button
              class="overflow-btn selora-menu-btn"
              aria-label="Selora menu"
              @click=${(e5) => {
                e5.stopPropagation();
                const opening = !this._showOverflowMenu;
                this._showOverflowMenu = opening;
                if (opening && this.narrow) this._showSidebar = false;
              }}
            >
              <ha-icon icon="mdi:dots-grid"></ha-icon>
            </button>
            ${
              this._showOverflowMenu
                ? x`
                  <div class="overflow-menu selora-menu">
                    <div class="overflow-section narrow-only">
                      <button
                        class="overflow-item"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._activeTab = "chat";
                          this._showSidebar = true;
                        }}
                      >
                        <ha-icon icon="mdi:chat-outline"></ha-icon>
                        Conversations
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "automations" ? "active" : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._activeTab = "automations";
                          this._showSidebar = false;
                          this._loadAutomations();
                        }}
                      >
                        <ha-icon icon="mdi:robot-outline"></ha-icon>
                        Automations
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "scenes" ? "active" : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._activeTab = "scenes";
                          this._showSidebar = false;
                          this._loadScenes();
                        }}
                      >
                        <ha-icon icon="mdi:palette-outline"></ha-icon>
                        Scenes
                      </button>
                      <div class="overflow-divider"></div>
                    </div>
                    <button
                      class="overflow-item ${this._activeTab === "settings" ? "active" : ""}"
                      @click=${() => {
                        this._showOverflowMenu = false;
                        this._activeTab = "settings";
                        this._showSidebar = false;
                        this._loadConfig();
                      }}
                    >
                      <ha-icon icon="mdi:cog-outline"></ha-icon>
                      Settings
                    </button>
                    <div class="overflow-divider"></div>
                    <a
                      class="overflow-item"
                      href="https://selorahomes.com/docs/selora-ai/"
                      target="_blank"
                      rel="noopener noreferrer"
                      @click=${() => {
                        this._showOverflowMenu = false;
                      }}
                    >
                      <ha-icon icon="mdi:book-open-variant"></ha-icon>
                      <span class="overflow-item-label">Documentation</span>
                      <ha-icon
                        icon="mdi:open-in-new"
                        class="overflow-item-external"
                      ></ha-icon>
                    </a>
                    <button
                      class="overflow-item"
                      @click=${() => {
                        this._showOverflowMenu = false;
                        this._openFeedback();
                      }}
                    >
                      <ha-icon icon="mdi:message-alert-outline"></ha-icon>
                      <span class="overflow-item-label">Give Feedback</span>
                    </button>
                    <a
                      class="overflow-item"
                      href="https://github.com/SeloraHomes/ha-selora-ai/issues"
                      target="_blank"
                      rel="noopener noreferrer"
                      @click=${() => {
                        this._showOverflowMenu = false;
                      }}
                    >
                      <ha-icon icon="mdi:github"></ha-icon>
                      <span class="overflow-item-label">GitHub Issues</span>
                      <ha-icon
                        icon="mdi:open-in-new"
                        class="overflow-item-external"
                      ></ha-icon>
                    </a>
                    <a
                      class="overflow-item"
                      href="https://gitlab.com/selorahomes/products/selora-ai/ha-integration/"
                      target="_blank"
                      rel="noopener noreferrer"
                      @click=${() => {
                        this._showOverflowMenu = false;
                      }}
                    >
                      <ha-icon icon="mdi:gitlab"></ha-icon>
                      <span class="overflow-item-label">GitLab Repository</span>
                      <ha-icon
                        icon="mdi:open-in-new"
                        class="overflow-item-external"
                      ></ha-icon>
                    </a>
                  </div>
                `
                : ""
            }
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
                      ${
                        this._deleteConfirmSessionId === s6.id
                          ? x`
                            <div class="session-item session-delete-confirm">
                              <span class="session-delete-confirm-label"
                                >Delete?</span
                              >
                              <div
                                style="display:flex;gap:6px;margin-left:auto;"
                              >
                                <button
                                  class="btn btn-sm"
                                  style="background:#ef4444;color:#fff;border-color:#ef4444;padding:3px 10px;font-size:12px;"
                                  @click=${(e5) => {
                                    e5.stopPropagation();
                                    this._confirmDeleteSession();
                                  }}
                                >
                                  Delete
                                </button>
                                <button
                                  class="btn btn-outline btn-sm"
                                  style="padding:3px 10px;font-size:12px;"
                                  @click=${(e5) => {
                                    e5.stopPropagation();
                                    this._deleteConfirmSessionId = null;
                                  }}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          `
                          : x`
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
                          `
                      }
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
          <selora-particles
            .count=${this._quotaAlert ? (this._isDark ? 1600 : 600) : this._isDark ? 1200 : 400}
            .color=${this._quotaAlert ? "#ef4444" : this._isDark ? "#C7AE6A" : this._primaryColor || "#03a9f4"}
            .maxOpacity=${this._quotaAlert ? 1 : this._isDark ? 1 : 0.5}
          ></selora-particles>
          ${this._renderQuotaBanner()}
          ${this._activeTab === "chat" ? this._renderChat() : ""}
          ${this._activeTab === "automations" ? this._renderAutomations() : ""}
          ${this._activeTab === "scenes" ? this._renderScenes() : ""}
          ${this._activeTab === "settings" ? this._renderSettings() : ""}
          ${this._activeTab === "usage" ? this._renderUsage() : ""}
        </div>
      </div>

      ${this._renderFeedbackModal()}
      ${
        this._deleteConfirmSessionId === "__bulk__"
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
                <div style="font-size:17px;font-weight:600;margin-bottom:8px;">
                  Delete Conversations
                </div>
                <div style="font-size:13px;opacity:0.7;margin-bottom:20px;">
                  Delete
                  ${Object.values(this._selectedSessionIds).filter(Boolean).length}
                  selected conversation(s)? This cannot be undone.
                </div>
                <div style="display:flex;gap:10px;justify-content:center;">
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
Object.assign(SeloraAIPanel.prototype, session_actions_exports);
Object.assign(SeloraAIPanel.prototype, suggestion_actions_exports);
Object.assign(SeloraAIPanel.prototype, chat_actions_exports);
Object.assign(SeloraAIPanel.prototype, automation_crud_exports);
Object.assign(SeloraAIPanel.prototype, automation_management_exports);
Object.assign(SeloraAIPanel.prototype, scene_actions_exports);
customElements.define("selora-ai", SeloraAIPanel);
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
