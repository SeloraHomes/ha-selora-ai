// node_modules/@lit/reactive-element/css-tag.js
var t = window;
var e = t.ShadowRoot && (void 0 === t.ShadyCSS || t.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype;
var s = Symbol();
var n = /* @__PURE__ */ new WeakMap();
var o = class {
  constructor(t3, e5, n5) {
    if (this._$cssResult$ = true, n5 !== s)
      throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
    this.cssText = t3, this.t = e5;
  }
  get styleSheet() {
    let t3 = this.o;
    const s6 = this.t;
    if (e && void 0 === t3) {
      const e5 = void 0 !== s6 && 1 === s6.length;
      e5 && (t3 = n.get(s6)), void 0 === t3 && ((this.o = t3 = new CSSStyleSheet()).replaceSync(this.cssText), e5 && n.set(s6, t3));
    }
    return t3;
  }
  toString() {
    return this.cssText;
  }
};
var r = (t3) => new o("string" == typeof t3 ? t3 : t3 + "", void 0, s);
var i = (t3, ...e5) => {
  const n5 = 1 === t3.length ? t3[0] : e5.reduce((e6, s6, n6) => e6 + ((t4) => {
    if (true === t4._$cssResult$)
      return t4.cssText;
    if ("number" == typeof t4)
      return t4;
    throw Error("Value passed to 'css' function must be a 'css' function result: " + t4 + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
  })(s6) + t3[n6 + 1], t3[0]);
  return new o(n5, t3, s);
};
var S = (s6, n5) => {
  e ? s6.adoptedStyleSheets = n5.map((t3) => t3 instanceof CSSStyleSheet ? t3 : t3.styleSheet) : n5.forEach((e5) => {
    const n6 = document.createElement("style"), o5 = t.litNonce;
    void 0 !== o5 && n6.setAttribute("nonce", o5), n6.textContent = e5.cssText, s6.appendChild(n6);
  });
};
var c = e ? (t3) => t3 : (t3) => t3 instanceof CSSStyleSheet ? ((t4) => {
  let e5 = "";
  for (const s6 of t4.cssRules)
    e5 += s6.cssText;
  return r(e5);
})(t3) : t3;

// node_modules/@lit/reactive-element/reactive-element.js
var s2;
var e2 = window;
var r2 = e2.trustedTypes;
var h = r2 ? r2.emptyScript : "";
var o2 = e2.reactiveElementPolyfillSupport;
var n2 = { toAttribute(t3, i5) {
  switch (i5) {
    case Boolean:
      t3 = t3 ? h : null;
      break;
    case Object:
    case Array:
      t3 = null == t3 ? t3 : JSON.stringify(t3);
  }
  return t3;
}, fromAttribute(t3, i5) {
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
} };
var a = (t3, i5) => i5 !== t3 && (i5 == i5 || t3 == t3);
var l = { attribute: true, type: String, converter: n2, reflect: false, hasChanged: a };
var d = "finalized";
var u = class extends HTMLElement {
  constructor() {
    super(), this._$Ei = /* @__PURE__ */ new Map(), this.isUpdatePending = false, this.hasUpdated = false, this._$El = null, this._$Eu();
  }
  static addInitializer(t3) {
    var i5;
    this.finalize(), (null !== (i5 = this.h) && void 0 !== i5 ? i5 : this.h = []).push(t3);
  }
  static get observedAttributes() {
    this.finalize();
    const t3 = [];
    return this.elementProperties.forEach((i5, s6) => {
      const e5 = this._$Ep(s6, i5);
      void 0 !== e5 && (this._$Ev.set(e5, s6), t3.push(e5));
    }), t3;
  }
  static createProperty(t3, i5 = l) {
    if (i5.state && (i5.attribute = false), this.finalize(), this.elementProperties.set(t3, i5), !i5.noAccessor && !this.prototype.hasOwnProperty(t3)) {
      const s6 = "symbol" == typeof t3 ? Symbol() : "__" + t3, e5 = this.getPropertyDescriptor(t3, s6, i5);
      void 0 !== e5 && Object.defineProperty(this.prototype, t3, e5);
    }
  }
  static getPropertyDescriptor(t3, i5, s6) {
    return { get() {
      return this[i5];
    }, set(e5) {
      const r4 = this[t3];
      this[i5] = e5, this.requestUpdate(t3, r4, s6);
    }, configurable: true, enumerable: true };
  }
  static getPropertyOptions(t3) {
    return this.elementProperties.get(t3) || l;
  }
  static finalize() {
    if (this.hasOwnProperty(d))
      return false;
    this[d] = true;
    const t3 = Object.getPrototypeOf(this);
    if (t3.finalize(), void 0 !== t3.h && (this.h = [...t3.h]), this.elementProperties = new Map(t3.elementProperties), this._$Ev = /* @__PURE__ */ new Map(), this.hasOwnProperty("properties")) {
      const t4 = this.properties, i5 = [...Object.getOwnPropertyNames(t4), ...Object.getOwnPropertySymbols(t4)];
      for (const s6 of i5)
        this.createProperty(s6, t4[s6]);
    }
    return this.elementStyles = this.finalizeStyles(this.styles), true;
  }
  static finalizeStyles(i5) {
    const s6 = [];
    if (Array.isArray(i5)) {
      const e5 = new Set(i5.flat(1 / 0).reverse());
      for (const i6 of e5)
        s6.unshift(c(i6));
    } else
      void 0 !== i5 && s6.push(c(i5));
    return s6;
  }
  static _$Ep(t3, i5) {
    const s6 = i5.attribute;
    return false === s6 ? void 0 : "string" == typeof s6 ? s6 : "string" == typeof t3 ? t3.toLowerCase() : void 0;
  }
  _$Eu() {
    var t3;
    this._$E_ = new Promise((t4) => this.enableUpdating = t4), this._$AL = /* @__PURE__ */ new Map(), this._$Eg(), this.requestUpdate(), null === (t3 = this.constructor.h) || void 0 === t3 || t3.forEach((t4) => t4(this));
  }
  addController(t3) {
    var i5, s6;
    (null !== (i5 = this._$ES) && void 0 !== i5 ? i5 : this._$ES = []).push(t3), void 0 !== this.renderRoot && this.isConnected && (null === (s6 = t3.hostConnected) || void 0 === s6 || s6.call(t3));
  }
  removeController(t3) {
    var i5;
    null === (i5 = this._$ES) || void 0 === i5 || i5.splice(this._$ES.indexOf(t3) >>> 0, 1);
  }
  _$Eg() {
    this.constructor.elementProperties.forEach((t3, i5) => {
      this.hasOwnProperty(i5) && (this._$Ei.set(i5, this[i5]), delete this[i5]);
    });
  }
  createRenderRoot() {
    var t3;
    const s6 = null !== (t3 = this.shadowRoot) && void 0 !== t3 ? t3 : this.attachShadow(this.constructor.shadowRootOptions);
    return S(s6, this.constructor.elementStyles), s6;
  }
  connectedCallback() {
    var t3;
    void 0 === this.renderRoot && (this.renderRoot = this.createRenderRoot()), this.enableUpdating(true), null === (t3 = this._$ES) || void 0 === t3 || t3.forEach((t4) => {
      var i5;
      return null === (i5 = t4.hostConnected) || void 0 === i5 ? void 0 : i5.call(t4);
    });
  }
  enableUpdating(t3) {
  }
  disconnectedCallback() {
    var t3;
    null === (t3 = this._$ES) || void 0 === t3 || t3.forEach((t4) => {
      var i5;
      return null === (i5 = t4.hostDisconnected) || void 0 === i5 ? void 0 : i5.call(t4);
    });
  }
  attributeChangedCallback(t3, i5, s6) {
    this._$AK(t3, s6);
  }
  _$EO(t3, i5, s6 = l) {
    var e5;
    const r4 = this.constructor._$Ep(t3, s6);
    if (void 0 !== r4 && true === s6.reflect) {
      const h3 = (void 0 !== (null === (e5 = s6.converter) || void 0 === e5 ? void 0 : e5.toAttribute) ? s6.converter : n2).toAttribute(i5, s6.type);
      this._$El = t3, null == h3 ? this.removeAttribute(r4) : this.setAttribute(r4, h3), this._$El = null;
    }
  }
  _$AK(t3, i5) {
    var s6;
    const e5 = this.constructor, r4 = e5._$Ev.get(t3);
    if (void 0 !== r4 && this._$El !== r4) {
      const t4 = e5.getPropertyOptions(r4), h3 = "function" == typeof t4.converter ? { fromAttribute: t4.converter } : void 0 !== (null === (s6 = t4.converter) || void 0 === s6 ? void 0 : s6.fromAttribute) ? t4.converter : n2;
      this._$El = r4, this[r4] = h3.fromAttribute(i5, t4.type), this._$El = null;
    }
  }
  requestUpdate(t3, i5, s6) {
    let e5 = true;
    void 0 !== t3 && (((s6 = s6 || this.constructor.getPropertyOptions(t3)).hasChanged || a)(this[t3], i5) ? (this._$AL.has(t3) || this._$AL.set(t3, i5), true === s6.reflect && this._$El !== t3 && (void 0 === this._$EC && (this._$EC = /* @__PURE__ */ new Map()), this._$EC.set(t3, s6))) : e5 = false), !this.isUpdatePending && e5 && (this._$E_ = this._$Ej());
  }
  async _$Ej() {
    this.isUpdatePending = true;
    try {
      await this._$E_;
    } catch (t4) {
      Promise.reject(t4);
    }
    const t3 = this.scheduleUpdate();
    return null != t3 && await t3, !this.isUpdatePending;
  }
  scheduleUpdate() {
    return this.performUpdate();
  }
  performUpdate() {
    var t3;
    if (!this.isUpdatePending)
      return;
    this.hasUpdated, this._$Ei && (this._$Ei.forEach((t4, i6) => this[i6] = t4), this._$Ei = void 0);
    let i5 = false;
    const s6 = this._$AL;
    try {
      i5 = this.shouldUpdate(s6), i5 ? (this.willUpdate(s6), null === (t3 = this._$ES) || void 0 === t3 || t3.forEach((t4) => {
        var i6;
        return null === (i6 = t4.hostUpdate) || void 0 === i6 ? void 0 : i6.call(t4);
      }), this.update(s6)) : this._$Ek();
    } catch (t4) {
      throw i5 = false, this._$Ek(), t4;
    }
    i5 && this._$AE(s6);
  }
  willUpdate(t3) {
  }
  _$AE(t3) {
    var i5;
    null === (i5 = this._$ES) || void 0 === i5 || i5.forEach((t4) => {
      var i6;
      return null === (i6 = t4.hostUpdated) || void 0 === i6 ? void 0 : i6.call(t4);
    }), this.hasUpdated || (this.hasUpdated = true, this.firstUpdated(t3)), this.updated(t3);
  }
  _$Ek() {
    this._$AL = /* @__PURE__ */ new Map(), this.isUpdatePending = false;
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
    void 0 !== this._$EC && (this._$EC.forEach((t4, i5) => this._$EO(i5, this[i5], t4)), this._$EC = void 0), this._$Ek();
  }
  updated(t3) {
  }
  firstUpdated(t3) {
  }
};
u[d] = true, u.elementProperties = /* @__PURE__ */ new Map(), u.elementStyles = [], u.shadowRootOptions = { mode: "open" }, null == o2 || o2({ ReactiveElement: u }), (null !== (s2 = e2.reactiveElementVersions) && void 0 !== s2 ? s2 : e2.reactiveElementVersions = []).push("1.6.3");

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
var d2 = (t3) => null === t3 || "object" != typeof t3 && "function" != typeof t3;
var c2 = Array.isArray;
var v = (t3) => c2(t3) || "function" == typeof (null == t3 ? void 0 : t3[Symbol.iterator]);
var a2 = "[ 	\n\f\r]";
var f = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g;
var _ = /-->/g;
var m = />/g;
var p = RegExp(`>|${a2}(?:([^\\s"'>=/]+)(${a2}*=${a2}*(?:[^ 	 // nosemgrep
\f\r"'\`<>=]|("|')|))|$)`, "g");
var g = /'/g;
var $ = /"/g;
var y = /^(?:script|style|textarea|title)$/i;
var w = (t3) => (i5, ...s6) => ({ _$litType$: t3, strings: i5, values: s6 });
var x = w(1);
var b = w(2);
var T = Symbol.for("lit-noChange");
var A = Symbol.for("lit-nothing");
var E = /* @__PURE__ */ new WeakMap();
var C = r3.createTreeWalker(r3, 129, null, false);
function P(t3, i5) {
  if (!Array.isArray(t3) || !t3.hasOwnProperty("raw"))
    throw Error("invalid template strings array");
  return void 0 !== e3 ? e3.createHTML(i5) : i5;
}
var V = (t3, i5) => {
  const s6 = t3.length - 1, e5 = [];
  let l5, r4 = 2 === i5 ? "<svg>" : "", u3 = f;
  for (let i6 = 0; i6 < s6; i6++) {
    const s7 = t3[i6];
    let d3, c3, v2 = -1, a4 = 0;
    for (; a4 < s7.length && (u3.lastIndex = a4, c3 = u3.exec(s7), null !== c3); )
      a4 = u3.lastIndex, u3 === f ? "!--" === c3[1] ? u3 = _ : void 0 !== c3[1] ? u3 = m : void 0 !== c3[2] ? (y.test(c3[2]) && (l5 = RegExp("</" + c3[2], "g")), u3 = p) : void 0 !== c3[3] && (u3 = p) : u3 === p ? ">" === c3[0] ? (u3 = null != l5 ? l5 : f, v2 = -1) : void 0 === c3[1] ? v2 = -2 : (v2 = u3.lastIndex - c3[2].length, d3 = c3[1], u3 = void 0 === c3[3] ? p : '"' === c3[3] ? $ : g) : u3 === $ || u3 === g ? u3 = p : u3 === _ || u3 === m ? u3 = f : (u3 = p, l5 = void 0); // nosemgrep
    const w2 = u3 === p && t3[i6 + 1].startsWith("/>") ? " " : "";
    r4 += u3 === f ? s7 + h2 : v2 >= 0 ? (e5.push(d3), s7.slice(0, v2) + o3 + s7.slice(v2) + n3 + w2) : s7 + n3 + (-2 === v2 ? (e5.push(void 0), i6) : w2);
  }
  return [P(t3, r4 + (t3[s6] || "<?>") + (2 === i5 ? "</svg>" : "")), e5];
};
var N = class _N {
  constructor({ strings: t3, _$litType$: i5 }, e5) {
    let h3;
    this.parts = [];
    let r4 = 0, d3 = 0;
    const c3 = t3.length - 1, v2 = this.parts, [a4, f2] = V(t3, i5);
    if (this.el = _N.createElement(a4, e5), C.currentNode = this.el.content, 2 === i5) {
      const t4 = this.el.content, i6 = t4.firstChild;
      i6.remove(), t4.append(...i6.childNodes);
    }
    for (; null !== (h3 = C.nextNode()) && v2.length < c3; ) {
      if (1 === h3.nodeType) {
        if (h3.hasAttributes()) {
          const t4 = [];
          for (const i6 of h3.getAttributeNames())
            if (i6.endsWith(o3) || i6.startsWith(n3)) {
              const s6 = f2[d3++];
              if (t4.push(i6), void 0 !== s6) {
                const t5 = h3.getAttribute(s6.toLowerCase() + o3).split(n3), i7 = /([.?@])?(.*)/.exec(s6);
                v2.push({ type: 1, index: r4, name: i7[2], strings: t5, ctor: "." === i7[1] ? H : "?" === i7[1] ? L : "@" === i7[1] ? z : k });
              } else
                v2.push({ type: 6, index: r4 });
            }
          for (const i6 of t4)
            h3.removeAttribute(i6);
        }
        if (y.test(h3.tagName)) {
          const t4 = h3.textContent.split(n3), i6 = t4.length - 1;
          if (i6 > 0) {
            h3.textContent = s3 ? s3.emptyScript : "";
            for (let s6 = 0; s6 < i6; s6++)
              h3.append(t4[s6], u2()), C.nextNode(), v2.push({ type: 2, index: ++r4 });
            h3.append(t4[i6], u2());
          }
        }
      } else if (8 === h3.nodeType)
        if (h3.data === l2)
          v2.push({ type: 2, index: r4 });
        else {
          let t4 = -1;
          for (; -1 !== (t4 = h3.data.indexOf(n3, t4 + 1)); )
            v2.push({ type: 7, index: r4 }), t4 += n3.length - 1;
        }
      r4++;
    }
  }
  static createElement(t3, i5) {
    const s6 = r3.createElement("template");
    return s6.innerHTML = t3, s6;
  }
};
function S2(t3, i5, s6 = t3, e5) {
  var o5, n5, l5, h3;
  if (i5 === T)
    return i5;
  let r4 = void 0 !== e5 ? null === (o5 = s6._$Co) || void 0 === o5 ? void 0 : o5[e5] : s6._$Cl;
  const u3 = d2(i5) ? void 0 : i5._$litDirective$;
  return (null == r4 ? void 0 : r4.constructor) !== u3 && (null === (n5 = null == r4 ? void 0 : r4._$AO) || void 0 === n5 || n5.call(r4, false), void 0 === u3 ? r4 = void 0 : (r4 = new u3(t3), r4._$AT(t3, s6, e5)), void 0 !== e5 ? (null !== (l5 = (h3 = s6)._$Co) && void 0 !== l5 ? l5 : h3._$Co = [])[e5] = r4 : s6._$Cl = r4), void 0 !== r4 && (i5 = S2(t3, r4._$AS(t3, i5.values), r4, e5)), i5;
}
var M = class {
  constructor(t3, i5) {
    this._$AV = [], this._$AN = void 0, this._$AD = t3, this._$AM = i5;
  }
  get parentNode() {
    return this._$AM.parentNode;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  u(t3) {
    var i5;
    const { el: { content: s6 }, parts: e5 } = this._$AD, o5 = (null !== (i5 = null == t3 ? void 0 : t3.creationScope) && void 0 !== i5 ? i5 : r3).importNode(s6, true);
    C.currentNode = o5;
    let n5 = C.nextNode(), l5 = 0, h3 = 0, u3 = e5[0];
    for (; void 0 !== u3; ) {
      if (l5 === u3.index) {
        let i6;
        2 === u3.type ? i6 = new R(n5, n5.nextSibling, this, t3) : 1 === u3.type ? i6 = new u3.ctor(n5, u3.name, u3.strings, this, t3) : 6 === u3.type && (i6 = new Z(n5, this, t3)), this._$AV.push(i6), u3 = e5[++h3];
      }
      l5 !== (null == u3 ? void 0 : u3.index) && (n5 = C.nextNode(), l5++);
    }
    return C.currentNode = r3, o5;
  }
  v(t3) {
    let i5 = 0;
    for (const s6 of this._$AV)
      void 0 !== s6 && (void 0 !== s6.strings ? (s6._$AI(t3, s6, i5), i5 += s6.strings.length - 2) : s6._$AI(t3[i5])), i5++;
  }
};
var R = class _R {
  constructor(t3, i5, s6, e5) {
    var o5;
    this.type = 2, this._$AH = A, this._$AN = void 0, this._$AA = t3, this._$AB = i5, this._$AM = s6, this.options = e5, this._$Cp = null === (o5 = null == e5 ? void 0 : e5.isConnected) || void 0 === o5 || o5;
  }
  get _$AU() {
    var t3, i5;
    return null !== (i5 = null === (t3 = this._$AM) || void 0 === t3 ? void 0 : t3._$AU) && void 0 !== i5 ? i5 : this._$Cp;
  }
  get parentNode() {
    let t3 = this._$AA.parentNode;
    const i5 = this._$AM;
    return void 0 !== i5 && 11 === (null == t3 ? void 0 : t3.nodeType) && (t3 = i5.parentNode), t3;
  }
  get startNode() {
    return this._$AA;
  }
  get endNode() {
    return this._$AB;
  }
  _$AI(t3, i5 = this) {
    t3 = S2(this, t3, i5), d2(t3) ? t3 === A || null == t3 || "" === t3 ? (this._$AH !== A && this._$AR(), this._$AH = A) : t3 !== this._$AH && t3 !== T && this._(t3) : void 0 !== t3._$litType$ ? this.g(t3) : void 0 !== t3.nodeType ? this.$(t3) : v(t3) ? this.T(t3) : this._(t3);
  }
  k(t3) {
    return this._$AA.parentNode.insertBefore(t3, this._$AB);
  }
  $(t3) {
    this._$AH !== t3 && (this._$AR(), this._$AH = this.k(t3));
  }
  _(t3) {
    this._$AH !== A && d2(this._$AH) ? this._$AA.nextSibling.data = t3 : this.$(r3.createTextNode(t3)), this._$AH = t3;
  }
  g(t3) {
    var i5;
    const { values: s6, _$litType$: e5 } = t3, o5 = "number" == typeof e5 ? this._$AC(t3) : (void 0 === e5.el && (e5.el = N.createElement(P(e5.h, e5.h[0]), this.options)), e5);
    if ((null === (i5 = this._$AH) || void 0 === i5 ? void 0 : i5._$AD) === o5)
      this._$AH.v(s6);
    else {
      const t4 = new M(o5, this), i6 = t4.u(this.options);
      t4.v(s6), this.$(i6), this._$AH = t4;
    }
  }
  _$AC(t3) {
    let i5 = E.get(t3.strings);
    return void 0 === i5 && E.set(t3.strings, i5 = new N(t3)), i5;
  }
  T(t3) {
    c2(this._$AH) || (this._$AH = [], this._$AR());
    const i5 = this._$AH;
    let s6, e5 = 0;
    for (const o5 of t3)
      e5 === i5.length ? i5.push(s6 = new _R(this.k(u2()), this.k(u2()), this, this.options)) : s6 = i5[e5], s6._$AI(o5), e5++;
    e5 < i5.length && (this._$AR(s6 && s6._$AB.nextSibling, e5), i5.length = e5);
  }
  _$AR(t3 = this._$AA.nextSibling, i5) {
    var s6;
    for (null === (s6 = this._$AP) || void 0 === s6 || s6.call(this, false, true, i5); t3 && t3 !== this._$AB; ) {
      const i6 = t3.nextSibling;
      t3.remove(), t3 = i6;
    }
  }
  setConnected(t3) {
    var i5;
    void 0 === this._$AM && (this._$Cp = t3, null === (i5 = this._$AP) || void 0 === i5 || i5.call(this, t3));
  }
};
var k = class {
  constructor(t3, i5, s6, e5, o5) {
    this.type = 1, this._$AH = A, this._$AN = void 0, this.element = t3, this.name = i5, this._$AM = e5, this.options = o5, s6.length > 2 || "" !== s6[0] || "" !== s6[1] ? (this._$AH = Array(s6.length - 1).fill(new String()), this.strings = s6) : this._$AH = A;
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
      t3 = S2(this, t3, i5, 0), n5 = !d2(t3) || t3 !== this._$AH && t3 !== T, n5 && (this._$AH = t3);
    else {
      const e6 = t3;
      let l5, h3;
      for (t3 = o5[0], l5 = 0; l5 < o5.length - 1; l5++)
        h3 = S2(this, e6[s6 + l5], i5, l5), h3 === T && (h3 = this._$AH[l5]), n5 || (n5 = !d2(h3) || h3 !== this._$AH[l5]), h3 === A ? t3 = A : t3 !== A && (t3 += (null != h3 ? h3 : "") + o5[l5 + 1]), this._$AH[l5] = h3;
    }
    n5 && !e5 && this.j(t3);
  }
  j(t3) {
    t3 === A ? this.element.removeAttribute(this.name) : this.element.setAttribute(this.name, null != t3 ? t3 : "");
  }
};
var H = class extends k {
  constructor() {
    super(...arguments), this.type = 3;
  }
  j(t3) {
    this.element[this.name] = t3 === A ? void 0 : t3;
  }
};
var I = s3 ? s3.emptyScript : "";
var L = class extends k {
  constructor() {
    super(...arguments), this.type = 4;
  }
  j(t3) {
    t3 && t3 !== A ? this.element.setAttribute(this.name, I) : this.element.removeAttribute(this.name);
  }
};
var z = class extends k {
  constructor(t3, i5, s6, e5, o5) {
    super(t3, i5, s6, e5, o5), this.type = 5;
  }
  _$AI(t3, i5 = this) {
    var s6;
    if ((t3 = null !== (s6 = S2(this, t3, i5, 0)) && void 0 !== s6 ? s6 : A) === T)
      return;
    const e5 = this._$AH, o5 = t3 === A && e5 !== A || t3.capture !== e5.capture || t3.once !== e5.once || t3.passive !== e5.passive, n5 = t3 !== A && (e5 === A || o5);
    o5 && this.element.removeEventListener(this.name, this, e5), n5 && this.element.addEventListener(this.name, this, t3), this._$AH = t3;
  }
  handleEvent(t3) {
    var i5, s6;
    "function" == typeof this._$AH ? this._$AH.call(null !== (s6 = null === (i5 = this.options) || void 0 === i5 ? void 0 : i5.host) && void 0 !== s6 ? s6 : this.element, t3) : this._$AH.handleEvent(t3);
  }
};
var Z = class {
  constructor(t3, i5, s6) {
    this.element = t3, this.type = 6, this._$AN = void 0, this._$AM = i5, this.options = s6;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t3) {
    S2(this, t3);
  }
};
var j = { O: o3, P: n3, A: l2, C: 1, M: V, L: M, R: v, D: S2, I: R, V: k, H: L, N: z, U: H, F: Z };
var B = i2.litHtmlPolyfillSupport;
null == B || B(N, R), (null !== (t2 = i2.litHtmlVersions) && void 0 !== t2 ? t2 : i2.litHtmlVersions = []).push("2.8.0");
var D = (t3, i5, s6) => {
  var e5, o5;
  const n5 = null !== (e5 = null == s6 ? void 0 : s6.renderBefore) && void 0 !== e5 ? e5 : i5;
  let l5 = n5._$litPart$;
  if (void 0 === l5) {
    const t4 = null !== (o5 = null == s6 ? void 0 : s6.renderBefore) && void 0 !== o5 ? o5 : null;
    n5._$litPart$ = l5 = new R(i5.insertBefore(u2(), t4), t4, void 0, null != s6 ? s6 : {});
  }
  return l5._$AI(t3), l5;
};

// node_modules/lit-element/lit-element.js
var l3;
var o4;
var s4 = class extends u {
  constructor() {
    super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
  }
  createRenderRoot() {
    var t3, e5;
    const i5 = super.createRenderRoot();
    return null !== (t3 = (e5 = this.renderOptions).renderBefore) && void 0 !== t3 || (e5.renderBefore = i5.firstChild), i5;
  }
  update(t3) {
    const i5 = this.render();
    this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(t3), this._$Do = D(i5, this.renderRoot, this.renderOptions);
  }
  connectedCallback() {
    var t3;
    super.connectedCallback(), null === (t3 = this._$Do) || void 0 === t3 || t3.setConnected(true);
  }
  disconnectedCallback() {
    var t3;
    super.disconnectedCallback(), null === (t3 = this._$Do) || void 0 === t3 || t3.setConnected(false);
  }
  render() {
    return T;
  }
};
s4.finalized = true, s4._$litElement$ = true, null === (l3 = globalThis.litElementHydrateSupport) || void 0 === l3 || l3.call(globalThis, { LitElement: s4 });
var n4 = globalThis.litElementPolyfillSupport;
null == n4 || n4({ LitElement: s4 });
(null !== (o4 = globalThis.litElementVersions) && void 0 !== o4 ? o4 : globalThis.litElementVersions = []).push("3.3.3");

// node_modules/lit-html/directive.js
var e4 = (t3) => (...e5) => ({ _$litDirective$: t3, values: e5 });
var i3 = class {
  constructor(t3) {
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AT(t3, e5, i5) {
    this._$Ct = t3, this._$AM = e5, this._$Ci = i5;
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
var a3 = (o5, l5 = s5) => o5._$AH = l5;

// node_modules/lit-html/directives/keyed.js
var i4 = e4(class extends i3 {
  constructor() {
    super(...arguments), this.key = A;
  }
  render(r4, t3) {
    return this.key = r4, t3;
  }
  update(r4, [t3, e5]) {
    return t3 !== this.key && (a3(r4), this.key = t3), e5;
  }
});

// src/panel.js
function renderMarkdown(text) {
  if (!text)
    return "";
  let escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  escaped = escaped.replace(/```([\s\S]*?)```/g, '<pre style="background:#2d2d2d;color:#f8f8f2;padding:10px;border-radius:6px;font-size:12px;overflow-x:auto;margin:8px 0;">$1</pre>');
  escaped = escaped.replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.1);padding:2px 5px;border-radius:3px;font-size:13px;">$1</code>');
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  escaped = escaped.replace(/\*(.+?)\*/g, "<em>$1</em>");
  escaped = escaped.replace(/^(\d+)\.\s+(.+)$/gm, '<div style="margin:4px 0 4px 8px;"><strong>$1.</strong> $2</div>');
  escaped = escaped.replace(/^[-•]\s+(.+)$/gm, '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(255,255,255,0.15);">$1</div>');
  escaped = escaped.replace(/\n/g, "<br>");
  return escaped;
}
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
      // Automations sub-tab
      _automationsSubTab: { type: String },
      // Inline card tabs (flow / yaml / history)
      _cardActiveTab: { type: Object },
      // Bulk edit mode
      _bulkEditMode: { type: Boolean },
      // Proactive suggestions (pattern-based)
      _proactiveSuggestions: { type: Array },
      _loadingProactive: { type: Boolean },
      _proactiveExpanded: { type: Object },
      _acceptingProactive: { type: Object },
      _dismissingProactive: { type: Object },
      _showProactive: { type: Boolean }
    };
  }
  constructor() {
    super();
    this._sessions = [];
    this._activeSessionId = null;
    this._messages = [];
    this._input = "";
    this._loading = false;
    this._showSidebar = true;
    this._activeTab = "chat";
    this._suggestions = [];
    this._automations = [];
    this._expandedAutomations = {};
    this._automationsSubTab = "my_automations";
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
    this._proactiveSuggestions = [];
    this._loadingProactive = false;
    this._proactiveExpanded = {};
    this._acceptingProactive = {};
    this._dismissingProactive = {};
    this._showProactive = true;
  }
  connectedCallback() {
    super.connectedCallback();
    this._checkTabParam();
    this._loadSessions();
    this._loadSuggestions();
    this._loadAutomations();
  }
  _checkTabParam() {
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab === "automations" || tab === "settings") {
      this._activeTab = tab;
      const url = new URL(window.location);
      url.searchParams.delete("tab");
      window.history.replaceState({}, "", url);
    }
  }
  // -------------------------------------------------------------------------
  // Data loaders
  // -------------------------------------------------------------------------
  async _loadSessions() {
    try {
      const sessions = await this.hass.callWS({ type: "selora_ai/get_sessions" });
      this._sessions = sessions || [];
      if (!this._activeSessionId && this._sessions.length > 0) {
        await this._openSession(this._sessions[0].id);
      }
    } catch (err) {
      console.error("Failed to load sessions", err);
    }
  }
  async _openSession(sessionId) {
    try {
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: sessionId
      });
      this._activeSessionId = session.id;
      this._messages = session.messages || [];
      this._activeTab = "chat";
      if (this.narrow)
        this._showSidebar = false;
    } catch (err) {
      console.error("Failed to open session", err);
    }
  }
  async _newSession() {
    try {
      const { session_id } = await this.hass.callWS({ type: "selora_ai/new_session" });
      this._activeSessionId = session_id;
      this._messages = [];
      this._activeTab = "chat";
      this._welcomeKey = (this._welcomeKey || 0) + 1;
      await this._loadSessions();
      if (this.narrow)
        this._showSidebar = false;
    } catch (err) {
      console.error("Failed to create session", err);
    }
  }
  async _newAutomationChat(name) {
    if (!name || !name.trim())
      return;
    const trimmed = name.trim();
    this._showNewAutoDialog = false;
    this.requestUpdate();
    try {
      const { session_id } = await this.hass.callWS({ type: "selora_ai/new_session" });
      await Promise.all([
        this.hass.callWS({ type: "selora_ai/rename_session", session_id, title: trimmed }).catch(() => {
        }),
        this.hass.callWS({ type: "selora_ai/create_draft", alias: trimmed, session_id }).catch(() => {
        })
      ]);
      this._activeSessionId = session_id;
      this._messages = [];
      this._input = `Create a new automation called "${trimmed}". It should `;
      this._activeTab = "chat";
      if (this.narrow)
        this._showSidebar = false;
      this.requestUpdate();
      await this.updateComplete;
      const textfield = this.shadowRoot?.querySelector("ha-textfield");
      if (textfield)
        textfield.focus();
      this._loadAutomations();
      this._loadSessions();
    } catch (err) {
      console.error("Failed to create automation chat session", err);
    }
  }
  _renderNewAutomationDialog() {
    if (!this._showNewAutoDialog)
      return "";
    return x`
      <div class="modal-overlay" @click=${() => {
      this._showNewAutoDialog = false;
    }}>
        <div class="modal-content" style="max-width:420px;" @click=${(e5) => e5.stopPropagation()}>
          <h3 style="margin:0 0 16px;">New Automation</h3>
          <label style="font-size:13px;font-weight:500;display:block;margin-bottom:6px;">Automation name</label>
          <div style="display:flex;gap:8px;align-items:center;">
            <input type="text" placeholder="e.g. Turn off lights at midnight"
              style="flex:1;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;font-size:14px;background:var(--card-background-color);color:var(--primary-text-color);box-sizing:border-box;"
              .value=${this._newAutoName}
              @input=${(e5) => {
      this._newAutoName = e5.target.value;
    }}
              @keydown=${(e5) => {
      if (e5.key === "Enter")
        this._newAutomationChat(this._newAutoName);
    }}>
            <button class="btn btn-outline" style="padding:8px 10px;flex-shrink:0;" title="AI Suggest"
              ?disabled=${this._suggestingName}
              @click=${() => this._suggestAutomationName()}>
              ${this._suggestingName ? x`<span class="spinner green"></span>` : x`<ha-icon icon="mdi:auto-fix" style="--mdc-icon-size:18px;"></ha-icon>`}
            </button>
          </div>
          ${this._suggestingName ? x`<div style="font-size:12px;color:var(--secondary-text-color);margin-top:6px;">Asking AI for a suggestion…</div>` : ""}
          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;">
            <button class="btn btn-outline" @click=${() => {
      this._showNewAutoDialog = false;
    }}>Cancel</button>
            <button class="btn btn-primary" ?disabled=${!this._newAutoName?.trim()}
              @click=${() => this._newAutomationChat(this._newAutoName)}>
              <ha-icon icon="mdi:chat-processing-outline" style="--mdc-icon-size:14px;"></ha-icon>
              Create in Chat
            </button>
          </div>
        </div>
      </div>
    `;
  }
  async _suggestAutomationName() {
    this._suggestingName = true;
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/chat",
        message: "Suggest one short, descriptive automation name for my smart home based on my devices and current setup. Reply with ONLY the automation name, nothing else. No quotes, no explanation."
      });
      const name = (result?.response || "").trim().replace(/^["']|["']$/g, "");
      if (name)
        this._newAutoName = name;
      if (result?.session_id) {
        this.hass.callWS({ type: "selora_ai/delete_session", session_id: result.session_id }).catch(() => {
        });
        this._loadSessions();
      }
    } catch (err) {
      console.error("Failed to suggest name", err);
      this._showToast("Failed to generate suggestion \u2014 check LLM config", "error");
    } finally {
      this._suggestingName = false;
    }
  }
  async _deleteSession(sessionId, evt) {
    evt.stopPropagation();
    if (!confirm("Delete this conversation?"))
      return;
    try {
      await this.hass.callWS({ type: "selora_ai/delete_session", session_id: sessionId });
      if (this._activeSessionId === sessionId) {
        this._activeSessionId = null;
        this._messages = [];
      }
      await this._loadSessions();
    } catch (err) {
      console.error("Failed to delete session", err);
    }
  }
  async _loadSuggestions() {
    try {
      const suggestions = await this.hass.callWS({ type: "selora_ai/get_suggestions" });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Failed to load suggestions", err);
    }
  }
  async _triggerGenerateSuggestions() {
    this._generatingSuggestions = true;
    try {
      const suggestions = await this.hass.callWS({ type: "selora_ai/generate_suggestions" });
      this._suggestions = suggestions || [];
      if (this._suggestions.length > 0) {
        this._showToast(`Generated ${this._suggestions.length} recommendation(s)`, "success");
      } else {
        this._showToast("Analysis complete \u2014 no new suggestions at this time", "info");
      }
    } catch (err) {
      console.error("Failed to generate suggestions", err);
      this._showToast("Failed to generate suggestions: " + (err.message || "unknown error"), "error");
    } finally {
      this._generatingSuggestions = false;
    }
  }
  async _loadAutomations() {
    try {
      const [automations, drafts] = await Promise.all([
        this.hass.callWS({ type: "selora_ai/get_automations" }),
        this.hass.callWS({ type: "selora_ai/get_drafts" }).catch(() => [])
      ]);
      const draftCards = (drafts || []).map((d3) => ({
        entity_id: `draft_${d3.draft_id}`,
        alias: d3.alias,
        state: "off",
        is_selora: true,
        automation_id: "",
        last_triggered: null,
        _draft: true,
        _draft_id: d3.draft_id,
        _linked_session: d3.session_id
      }));
      this._automations = [...draftCards, ...automations || []];
      const validIds = new Set(this._automations.map((a4) => a4.automation_id).filter(Boolean));
      this._selectedAutomationIds = Object.fromEntries(
        Object.entries(this._selectedAutomationIds || {}).filter(([id, selected]) => selected && validIds.has(id))
      );
    } catch (err) {
      console.error("Failed to load automations", err);
    }
    this._loadProactiveSuggestions();
  }
  async _loadProactiveSuggestions() {
    this._loadingProactive = true;
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/get_proactive_suggestions",
        status: "pending"
      });
      this._proactiveSuggestions = suggestions || [];
    } catch (err) {
      console.error("Failed to load proactive suggestions", err);
      this._proactiveSuggestions = [];
    }
    this._loadingProactive = false;
  }
  async _acceptProactiveSuggestion(suggestionId, editedYaml) {
    this._acceptingProactive = { ...this._acceptingProactive, [suggestionId]: true };
    try {
      if (editedYaml) {
        await this.hass.callWS({
          type: "selora_ai/accept_suggestion_with_edits",
          suggestion_id: suggestionId,
          automation_yaml: editedYaml
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/update_proactive_suggestion",
          suggestion_id: suggestionId,
          action: "accepted"
        });
      }
      this._showToast("Suggestion accepted \u2014 automation created", "success");
      await this._loadAutomations();
    } catch (err) {
      console.error("Failed to accept suggestion", err);
      this._showToast("Failed to accept suggestion", "error");
    }
    this._acceptingProactive = { ...this._acceptingProactive, [suggestionId]: false };
  }
  async _dismissProactiveSuggestion(suggestionId) {
    this._dismissingProactive = { ...this._dismissingProactive, [suggestionId]: true };
    try {
      await this.hass.callWS({
        type: "selora_ai/update_proactive_suggestion",
        suggestion_id: suggestionId,
        action: "dismissed"
      });
      this._proactiveSuggestions = this._proactiveSuggestions.filter(
        (s6) => s6.suggestion_id !== suggestionId
      );
      this._showToast("Suggestion dismissed", "info");
    } catch (err) {
      console.error("Failed to dismiss suggestion", err);
    }
    this._dismissingProactive = { ...this._dismissingProactive, [suggestionId]: false };
  }
  async _snoozeProactiveSuggestion(suggestionId) {
    try {
      await this.hass.callWS({
        type: "selora_ai/update_proactive_suggestion",
        suggestion_id: suggestionId,
        action: "snoozed"
      });
      this._proactiveSuggestions = this._proactiveSuggestions.filter(
        (s6) => s6.suggestion_id !== suggestionId
      );
      this._showToast("Suggestion snoozed for 24h", "info");
    } catch (err) {
      console.error("Failed to snooze suggestion", err);
    }
  }
  async _triggerPatternScan() {
    this._loadingProactive = true;
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/trigger_pattern_scan"
      });
      this._showToast(`Scan complete \u2014 ${result.patterns_found} patterns found`, "success");
      await this._loadProactiveSuggestions();
    } catch (err) {
      console.error("Pattern scan failed", err);
      this._showToast("Pattern scan failed", "error");
    }
    this._loadingProactive = false;
  }
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
    if (!this._config || this._savingConfig)
      return;
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
      await this.hass.callWS({ type: "selora_ai/update_config", config: payload });
      this._newApiKey = "";
      await this._loadConfig();
      this._showToast("Configuration saved.", "success");
    } catch (err) {
      this._showToast("Failed to save configuration: " + err.message, "error");
    } finally {
      this._savingConfig = false;
    }
  }
  // -------------------------------------------------------------------------
  // Messaging
  // -------------------------------------------------------------------------
  _quickStart(message) {
    this._input = message;
    this._sendMessage();
  }
  async _sendMessage() {
    if (!this._input.trim() || this._loading)
      return;
    const userMsg = this._input;
    this._messages = [...this._messages, { role: "user", content: userMsg }];
    this._input = "";
    this._loading = true;
    const assistantMsg = { role: "assistant", content: "", _streaming: true };
    this._messages = [...this._messages, assistantMsg];
    try {
      const subscribePayload = {
        type: "selora_ai/chat_stream",
        message: userMsg
      };
      if (this._activeSessionId) {
        subscribePayload.session_id = this._activeSessionId;
      }
      const unsub = await this.hass.connection.subscribeMessage(
        (event) => {
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
            assistantMsg.automation_message_index = event.automation_message_index ?? null;
            assistantMsg.refining_automation_id = event.refining_automation_id || null;
            assistantMsg._streaming = false;
            this._messages = [...this._messages];
            this._loading = false;
            if (event.validation_error) {
              this._showToast(`Automation validation failed: ${event.validation_error}`, "error");
            }
            if (event.session_id) {
              if (event.session_id !== this._activeSessionId) {
                this._activeSessionId = event.session_id;
              }
              this._loadSessions();
            }
            unsub();
          } else if (event.type === "error") {
            assistantMsg.content = "Sorry, I encountered an error: " + event.message;
            assistantMsg._streaming = false;
            this._messages = [...this._messages];
            this._loading = false;
            unsub();
          }
        },
        subscribePayload
      );
    } catch (err) {
      assistantMsg.content = "Sorry, I encountered an error: " + err.message;
      assistantMsg._streaming = false;
      this._messages = [...this._messages];
      this._loading = false;
    }
  }
  _requestScrollChat() {
    if (!this._scrollPending) {
      this._scrollPending = true;
      requestAnimationFrame(() => {
        this._scrollPending = false;
        const container = this.shadowRoot.getElementById("chat-messages");
        if (container)
          container.scrollTop = container.scrollHeight;
      });
    }
  }
  // -------------------------------------------------------------------------
  // Automation actions
  // -------------------------------------------------------------------------
  // -------------------------------------------------------------------------
  // Refinement helpers
  // -------------------------------------------------------------------------
  _getRefiningAutomationId(msgIndex = null) {
    const msg = msgIndex == null ? null : this._messages[msgIndex];
    if (msg?.refining_automation_id)
      return msg.refining_automation_id;
    if (msg?.automation_id)
      return msg.automation_id;
    if (msg?.automation?.id)
      return msg.automation.id;
    for (const m2 of this._messages) {
      if (m2.automation_status === "refining") {
        if (m2.automation_id)
          return m2.automation_id;
        if (m2.automation?.id)
          return m2.automation.id;
      }
    }
    return null;
  }
  async _loadLineage(automationId) {
    this._loadingLineage = { ...this._loadingLineage, [automationId]: true };
    this.requestUpdate();
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_automation_lineage",
        automation_id: automationId
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
  async _acceptAutomation(msgIndex, automation) {
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
            version_message: "Refined via chat"
          });
        } else {
          await this.hass.callWS({
            type: "selora_ai/create_automation",
            automation,
            session_id: this._activeSessionId
          });
        }
      } else {
        await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation,
          session_id: this._activeSessionId
        });
      }
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "saved"
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId
      });
      this._messages = session.messages || [];
      await this._removeDraftForSession(this._activeSessionId);
      await this._loadAutomations();
      const actionLabel = refiningId ? "updated." : automation?.initial_state === true ? "created and added to your system." : "created and added to your system (disabled by default for review).";
      this._messages = [
        ...this._messages,
        {
          role: "assistant",
          content: `Automation "${automation.alias}" ${actionLabel}`,
          timestamp: (/* @__PURE__ */ new Date()).toISOString()
        }
      ];
    } catch (err) {
      this._showToast("Failed to save automation: " + err.message, "error");
    }
  }
  async _removeDraftForSession(sessionId) {
    if (!sessionId)
      return;
    try {
      const draft = this._automations.find((a4) => a4._draft && a4._linked_session === sessionId);
      if (draft && draft._draft_id) {
        await this.hass.callWS({ type: "selora_ai/remove_draft", draft_id: draft._draft_id });
      }
    } catch (err) {
      console.error("Failed to remove draft for session", err);
    }
  }
  async _dismissDraft(draftId) {
    if (!draftId)
      return;
    try {
      await this.hass.callWS({ type: "selora_ai/remove_draft", draft_id: draftId });
      await this._loadAutomations();
      this._showToast("Draft dismissed.", "info");
    } catch (err) {
      console.error("Failed to dismiss draft", err);
      this._showToast("Failed to dismiss draft: " + err.message, "error");
    }
  }
  async _declineAutomation(msgIndex) {
    try {
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "declined"
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId
      });
      this._messages = session.messages || [];
    } catch (err) {
      console.error("Failed to decline automation", err);
    }
  }
  async _refineAutomation(msgIndex, automation, description) {
    try {
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "refining"
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId
      });
      this._messages = session.messages || [];
    } catch (err) {
      console.error("Failed to mark automation as refining", err);
    }
    const ctx = description ? ` (${description})` : "";
    this._input = `Refine "${automation.alias}"${ctx}: `;
    this.shadowRoot.querySelector("ha-textfield")?.focus();
  }
  async _createAutomationFromSuggestion(automation) {
    try {
      await this.hass.callWS({ type: "selora_ai/create_automation", automation });
      await this._loadAutomations();
      this._showToast(`Automation "${automation.alias}" created.`, "success");
    } catch (err) {
      this._showToast("Failed to create automation: " + err.message, "error");
    }
  }
  _discardSuggestion(suggestion) {
    this._suggestions = this._suggestions.filter((s6) => s6 !== suggestion);
  }
  // Accept automation — if the user edited the YAML, send the edited version
  async _acceptAutomationWithEdits(msgIndex, automation, yamlKey) {
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
            version_message: "Refined via chat (with edits)"
          });
        } else {
          await this.hass.callWS({
            type: "selora_ai/apply_automation_yaml",
            yaml_text: edited,
            session_id: this._activeSessionId
          });
        }
        await this.hass.callWS({
          type: "selora_ai/set_automation_status",
          session_id: this._activeSessionId,
          message_index: msgIndex,
          status: "saved"
        });
        const session = await this.hass.callWS({ type: "selora_ai/get_session", session_id: this._activeSessionId });
        this._messages = session.messages || [];
        await this._loadAutomations();
        const actionLabel = refiningId ? "updated with your edits." : automation?.initial_state === true ? "created with your edits." : "created with your edits (disabled by default for review).";
        this._messages = [...this._messages, {
          role: "assistant",
          content: `Automation "${automation.alias}" ${actionLabel}`,
          timestamp: (/* @__PURE__ */ new Date()).toISOString()
        }];
      } catch (err) {
        this._showToast("Failed to save automation from edited YAML: " + err.message, "error");
      } finally {
        this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
        this.requestUpdate();
      }
    } else {
      await this._acceptAutomation(msgIndex, automation);
    }
  }
  async _createSuggestionWithEdits(auto, yamlKey, originalYaml) {
    const edited = this._editedYaml[yamlKey];
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();
      if (edited && edited !== originalYaml) {
        await this.hass.callWS({ type: "selora_ai/apply_automation_yaml", yaml_text: edited });
      } else {
        await this.hass.callWS({ type: "selora_ai/create_automation", automation: auto });
      }
      await this._loadAutomations();
      this._showToast(`Automation "${auto.alias}" created.`, "success");
    } catch (err) {
      this._showToast("Failed to create automation: " + err.message, "error");
    } finally {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
      this.requestUpdate();
    }
  }
  async _saveActiveAutomationYaml(automationId, yamlKey) {
    const edited = this._editedYaml[yamlKey];
    if (!edited)
      return;
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();
      await this.hass.callWS({
        type: "selora_ai/update_automation_yaml",
        automation_id: automationId,
        yaml_text: edited
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
  _initYamlEdit(key, originalYaml) {
    if (this._editedYaml[key] === void 0) {
      this._editedYaml = { ...this._editedYaml, [key]: originalYaml };
    }
  }
  _onYamlInput(key, value) {
    this._editedYaml = { ...this._editedYaml, [key]: value };
    this.requestUpdate();
  }
  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig();
  }
  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }
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
  // -------------------------------------------------------------------------
  // Scroll to bottom on new messages
  // -------------------------------------------------------------------------
  updated(changedProps) {
    if (changedProps.has("hass")) {
      this._checkTabParam();
    }
    if (changedProps.has("_messages") && this._activeTab === "chat") {
      const container = this.shadowRoot.getElementById("chat-messages");
      if (container)
        container.scrollTop = container.scrollHeight;
    }
  }
  // -------------------------------------------------------------------------
  // Styles
  // -------------------------------------------------------------------------
  static get styles() {
    return i`
      :host {
        display: flex;
        height: 100%;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, roboto, sans-serif);
        overflow: hidden;
      }

      /* ---- Sidebar (session list) ---- */
      .sidebar {
        width: 0;
        min-width: 0;
        display: flex;
        flex-direction: column;
        background: var(--sidebar-background-color, var(--card-background-color));
        border-right: 1px solid var(--divider-color);
        overflow: hidden;
        transition: width 0.25s ease, min-width 0.25s ease;
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
        letter-spacing: 0.08em;
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
      .session-item {
        padding: 12px 16px;
        cursor: pointer;
        border-bottom: 1px solid var(--divider-color);
        display: flex;
        align-items: flex-start;
        gap: 8px;
        position: relative;
        transition: background 0.15s;
      }
      .session-item:hover {
        background: var(--secondary-background-color);
      }
      .session-item.active {
        background: rgba(245, 158, 11, 0.12);
        border-left: 3px solid #f59e0b;
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
      .new-chat-btn {
        margin: 12px;
        display: block;
      }

      /* ---- Main area ---- */
      .main {
        flex: 1;
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .header {
        background: var(--app-header-background-color);
        color: var(--app-header-text-color);
        box-shadow: var(--card-box-shadow);
        z-index: 2;
        flex-shrink: 0;
      }
      .header-top {
        padding: 14px 16px;
        font-size: 18px;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .header-top ha-icon-button {
        margin-right: 4px;
        display: none;
      }
      :host([narrow]) .header-top ha-icon-button {
        display: inline-flex;
      }
      .tabs {
        display: flex;
        padding: 0 8px;
        border-top: 1px solid rgba(255,255,255,0.1);
      }
      .tab {
        padding: 10px 20px;
        cursor: pointer;
        font-weight: 500;
        font-size: 13px;
        text-transform: uppercase;
        opacity: 0.65;
        border-bottom: 3px solid transparent;
        transition: all 0.2s;
      }
      .tab:hover { opacity: 1; }
      .tab.active {
        opacity: 1;
        border-bottom-color: var(--accent-color, #ff9800);
      }

      /* ---- Chat ---- */
      .chat-pane {
        flex: 1;
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .chat-messages {
        flex: 1;
        overflow-y: auto;
        padding: 20px 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      @keyframes fadeInUp {
        from { opacity: 0; transform: translateY(18px); }
        to { opacity: 1; transform: translateY(0); }
      }
      @keyframes logoEntrance {
        0% { opacity: 0; transform: scale(0.6) translateY(12px); }
        60% { opacity: 1; transform: scale(1.06) translateY(-2px); }
        100% { opacity: 1; transform: scale(1) translateY(0); }
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
      }
      .empty-state.welcome > * {
        animation: fadeInUp 0.5s ease both;
      }
      .empty-state.welcome > img:first-child {
        animation: logoEntrance 0.7s cubic-bezier(0.34, 1.56, 0.64, 1) both;
      }
      .empty-state.welcome > :nth-child(2) { animation-delay: 0.15s; }
      .empty-state.welcome > :nth-child(3) { animation-delay: 0.25s; }
      .empty-state.welcome > :nth-child(4) { animation-delay: 0.35s; }
      .empty-state.welcome > :nth-child(5) { animation-delay: 0.4s; }
      .empty-state.welcome > :nth-child(6) { animation-delay: 0.45s; }
      .empty-state.welcome > :nth-child(7) { animation-delay: 0.5s; }
      .empty-state ha-icon {
        --mdc-icon-size: 56px;
      }
      .welcome-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
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
        background: #f59e0b !important;
        color: #1a1a1a !important;
        border-bottom-right-radius: 4px;
      }
      .bubble.assistant {
        align-self: flex-start;
        background: var(--card-background-color);
        box-shadow: var(--card-box-shadow);
        border-bottom-left-radius: 4px;
      }
      .bubble-meta {
        font-size: 10px;
        opacity: 0.5;
        margin-top: 3px;
      }
      .bubble.user + .bubble-meta { align-self: flex-end; }
      .bubble.assistant + .bubble-meta { align-self: flex-start; }
      .bubble.assistant strong { color: #f59e0b; }

      /* ---- Automation proposal card ---- */
      .proposal-card {
        margin-top: 12px;
        border: 1px solid #f59e0b;
        border-radius: 10px;
        overflow: hidden;
        background: var(--primary-background-color);
      }
      .proposal-header {
        background: rgba(245, 158, 11, 0.1);
        padding: 10px 14px;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        display: flex;
        align-items: center;
        gap: 6px;
        color: #f59e0b;
      }
      .proposal-body {
        padding: 14px;
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
        background: rgba(var(--rgb-accent-color, 255, 152, 0), 0.08);
        border-left: 3px solid var(--accent-color, #ff9800);
        border-radius: 0 6px 6px 0;
      }
      .proposal-description-label {
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
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
      .yaml-toggle:hover { opacity: 1; }
      textarea.yaml-editor {
        width: 100%;
        box-sizing: border-box;
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 10px 12px;
        border-radius: 6px;
        font-size: 11px;
        font-family: 'Fira Code', 'Cascadia Code', monospace;
        line-height: 1.5;
        border: 1px solid rgba(255,255,255,0.12);
        resize: vertical;
        min-height: 140px;
        outline: none;
        transition: border-color 0.15s;
      }
      textarea.yaml-editor:focus {
        border-color: #f59e0b;
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
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 10px 12px;
        border-radius: 6px;
        font-size: 11px;
        overflow-x: auto;
        font-family: 'Fira Code', 'Cascadia Code', monospace;
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
        background: rgba(76,175,80,0.12);
        color: var(--success-color, #4caf50);
      }
      .proposal-status.declined {
        background: rgba(158,158,158,0.12);
        color: var(--secondary-text-color);
      }

      /* ---- Automation flowchart ---- */
      .flow-chart {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        margin: 10px 0 12px;
        font-size: 12px;
      }
      .flow-section { width: 100%; }
      .flow-label {
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.1em;
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
      .flow-node + .flow-node { margin-top: 3px; }
      .trigger-node,
      .condition-node,
      .action-node {
        background: rgba(var(--rgb-primary-text-color, 255,255,255), 0.06);
        border: 1px solid rgba(var(--rgb-primary-text-color, 255,255,255), 0.15);
        color: var(--primary-text-color);
      }
      .flow-arrow {
        font-size: 16px;
        line-height: 1;
        opacity: 0.35;
        padding: 3px 0 3px 4px;
      }
      .flow-arrow-sm {
        font-size: 13px;
        line-height: 1;
        opacity: 0.3;
        padding: 2px 0 2px 4px;
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
      .toggle-switch input { opacity: 0; width: 0; height: 0; }
      .toggle-track {
        position: absolute;
        inset: 0;
        border-radius: 11px;
        background: var(--divider-color);
        border: 1px solid rgba(0,0,0,0.15);
        transition: background 0.2s;
      }
      .toggle-track.on { background: #f59e0b; }
      .toggle-thumb {
        position: absolute;
        top: 3px;
        left: 3px;
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: white;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        transition: left 0.2s;
      }
      .toggle-track.on .toggle-thumb { left: 21px; }
      .toggle-label {
        font-size: 12px;
        font-weight: 600;
        color: var(--secondary-text-color);
      }
      .toggle-label.on { color: #f59e0b; }

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
      .btn {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        border: 1.5px solid transparent;
        background: transparent;
        transition: background 0.15s, opacity 0.15s;
        user-select: none;
        letter-spacing: 0.02em;
      }
      .btn:hover { opacity: 0.85; }
      .btn-primary {
        background: #f59e0b;
        border-color: #f59e0b;
        color: #1a1a1a;
      }
      .btn-success {
        background: var(--success-color, #4caf50);
        border-color: var(--success-color, #4caf50);
        color: white;
      }
      .btn-outline {
        border-color: var(--divider-color);
        color: var(--primary-text-color);
        background: var(--card-background-color);
      }
      .btn-outline:hover { border-color: #f59e0b; color: #f59e0b; }
      .btn-danger {
        border-color: var(--error-color, #f44336);
        color: var(--error-color, #f44336);
        background: transparent;
      }
      .btn-danger:hover { background: rgba(244,67,54,0.08); }
      .btn-warning {
        border-color: var(--warning-color, #ff9800);
        color: var(--warning-color, #ff9800);
        background: transparent;
      }
      .btn-warning:hover { background: rgba(255,152,0,0.08); }

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
      .burger-btn:hover { background: rgba(0,0,0,0.06); color: var(--primary-text-color); }
      .burger-dropdown {
        position: absolute;
        right: 0;
        top: 32px;
        background: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
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
      .burger-item:hover { background: rgba(var(--rgb-primary-color, 3,169,244), 0.08); }
      .burger-item.danger { color: var(--error-color, #f44336); }
      .burger-item.danger:hover { background: rgba(244,67,54,0.08); }

      /* ---- Card inline tabs (Flow / YAML / History) ---- */
      .card-tabs {
        display: flex;
        align-items: center;
        gap: 0;
        margin: 8px 0 0;
        border-top: 1px solid var(--divider-color);
        padding-top: 8px;
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
        border-bottom: 2px solid transparent;
        transition: color 0.15s, border-color 0.15s;
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }
      .card-tab:hover { color: var(--primary-text-color); }
      .card-tab.active {
        color: #f59e0b;
        border-bottom-color: #f59e0b;
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
      .card-chevron:hover { opacity: 0.8; }
      .card-chevron.open { transform: rotate(180deg); }
      .card-tab-sep {
        color: var(--divider-color);
        font-size: 12px;
        user-select: none;
      }

      /* ---- Filter input ---- */
      .sub-tabs {
        display: flex;
        gap: 0;
        margin-bottom: 12px;
        justify-content: center;
      }
      .sub-tab {
        padding: 8px 18px;
        border: none;
        background: none;
        font-size: 14px;
        font-weight: 500;
        color: var(--secondary-text-color);
        cursor: pointer;
        border-bottom: 2px solid transparent;
        transition: color 0.15s, border-color 0.15s;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .sub-tab:hover { color: var(--primary-text-color); }
      .sub-tab.active {
        color: #f59e0b;
        border-bottom-color: #f59e0b;
      }
      .sub-tab .badge {
        background: #f59e0b;
        color: #000;
        border-radius: 10px;
        padding: 1px 7px;
        font-size: 11px;
        font-weight: 600;
        min-width: 16px;
        text-align: center;
      }
      .filter-row {
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 12px;
        gap: 12px;
      }
      .filter-input-wrap {
        display: flex;
        align-items: center;
        gap: 6px;
        background: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 4px 10px;
        flex: 0 1 400px;
      }
      .filter-input-wrap input {
        border: none;
        background: transparent;
        color: var(--primary-text-color);
        font-size: 13px;
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
        width: 14px;
        height: 14px;
        margin: 0;
        accent-color: var(--primary-color);
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
        width: 14px;
        height: 14px;
        margin: 0;
        accent-color: var(--primary-color);
        cursor: pointer;
      }

      /* ---- Status indicator (inline) ---- */
      .status-indicator {
        font-size: 11px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 10px;
        flex-shrink: 0;
      }
      .status-indicator.on {
        color: var(--success-color, #4caf50);
        background: rgba(76,175,80,0.12);
      }
      .status-indicator.off {
        color: var(--secondary-text-color);
        background: rgba(158,158,158,0.12);
      }
      .btn-ghost {
        border-color: transparent;
        color: var(--secondary-text-color);
        background: transparent;
        font-size: 11px;
        padding: 4px 8px;
      }
      .btn-ghost:hover { color: var(--primary-text-color); background: rgba(0,0,0,0.06); border-color: var(--divider-color); }
      .btn-ghost.active { color: #b45309; border-color: rgba(245,158,11,0.35); background: rgba(245,158,11,0.05); }
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
      .expand-toggle:hover { opacity: 1; }

      /* ---- Spinner ---- */
      .spinner {
        display: inline-block;
        width: 18px;
        height: 18px;
        border: 2.5px solid rgba(0,0,0,0.1);
        border-top-color: #f59e0b;
        border-radius: 50%;
        animation: spin 0.7s linear infinite;
      }
      .spinner.green {
        border-color: rgba(76,175,80,0.2);
        border-top-color: var(--success-color, #4caf50);
      }
      @keyframes spin { to { transform: rotate(360deg); } }

      /* ---- Modal overlay ---- */
      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,0.5);
        z-index: 10001;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .modal-content {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        width: 90%;
      }

      /* ---- Automations grid (CSS Grid) ---- */
      .automations-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        gap: 10px;
        margin-bottom: 14px;
      }
      .automations-grid .card {
        margin-bottom: 0;
        padding: 12px 14px;
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .automations-grid .card-header {
        margin-bottom: 4px;
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

      /* ---- Chat input ---- */
      .chat-input {
        padding: 12px 16px;
        background: var(--card-background-color);
        border-top: 1px solid var(--divider-color);
        display: flex;
        gap: 10px;
        align-items: center;
        flex-shrink: 0;
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
      .typing-dot:nth-child(1) { animation-delay: 0s; }
      .typing-dot:nth-child(2) { animation-delay: 0.2s; }
      .typing-dot:nth-child(3) { animation-delay: 0.4s; }
      @keyframes typingBounce {
        0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
        40% { transform: scale(1); opacity: 1; }
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
      @keyframes blink {
        50% { opacity: 0; }
      }

      /* ---- Scroll view (automations / settings) ---- */
      .scroll-view {
        flex: 1;
        overflow-y: auto;
        padding: 16px 24px;
        max-width: 1200px;
        margin: 0 auto;
        width: 100%;
        box-sizing: border-box;
      }
      .card {
        background: var(--card-background-color);
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 14px;
        box-shadow: var(--card-box-shadow);
        border: 1px solid var(--divider-color);
      }
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 10px;
      }
      .card h3 { margin: 0; font-size: 16px; }
      .card p { margin: 6px 0; color: var(--secondary-text-color); font-size: 13px; }
      .chip {
        padding: 3px 9px;
        border-radius: 10px;
        font-size: 10px;
        font-weight: 700;
        color: white;
      }
      .chip.ai-managed { background: #f59e0b; }
      .chip.user-managed { background: #9e9e9e; }
      .chip.suggestion { background: #f59e0b; }
      pre { background: #1e1e2e; color: #cdd6f4; padding: 10px; border-radius: 6px; font-size: 11px; overflow-x: auto; }

      /* ---- Settings ---- */
      .settings-form { max-width: 600px; margin: 0 auto; }
      .form-group { margin-bottom: 22px; }
      .form-group label { display: block; margin-bottom: 6px; font-weight: 500; font-size: 14px; }
      .key-hint {
        font-size: 12px;
        opacity: 0.6;
        font-family: monospace;
        padding: 4px 8px;
        background: var(--secondary-background-color);
        border-radius: 4px;
        display: inline-block;
        margin-top: 4px;
      }
      .key-not-set { font-size: 12px; opacity: 0.5; font-style: italic; margin-top: 4px; }
      .save-bar { margin-top: 28px; display: flex; justify-content: flex-end; }

      /* Narrow overrides — sidebar overlays on small screens */
      :host([narrow]) .sidebar {
        position: absolute;
        left: 0; top: 0; bottom: 0;
        z-index: 10;
        width: 0;
        min-width: 0;
        transform: translateX(-100%);
        transition: transform 0.25s ease, width 0.25s ease, min-width 0.25s ease;
        box-shadow: 2px 0 8px rgba(0,0,0,0.2);
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
      .toast.info { background: #1f6feb; }
      .toast.success { background: #198754; }
      .toast.error { background: #dc3545; }
      .toast-close {
        margin-left: auto;
        cursor: pointer;
        opacity: 0.85;
      }
      .toast-close:hover {
        opacity: 1;
      }
    `;
  }
  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  render() {
    return x`
      <div class="sidebar ${this._showSidebar ? "open" : ""}" part="sidebar">
        <div class="sidebar-header">
          <span>Conversations</span>
        </div>
        <mwc-button class="new-chat-btn" outlined @click=${this._newSession}>
          + New Chat
        </mwc-button>
        <div class="session-list">
          ${this._sessions.length === 0 ? x`<div style="padding: 16px; font-size: 12px; opacity: 0.5;">No conversations yet.</div>` : this._sessions.map(
      (s6) => x`
                  <div
                    class="session-item ${s6.id === this._activeSessionId ? "active" : ""}"
                    @click=${() => this._openSession(s6.id)}
                  >
                    <div style="flex:1; min-width:0;">
                      <div class="session-title">${s6.title}</div>
                      <div class="session-meta">${this._formatDate(s6.updated_at)}</div>
                    </div>
                    <ha-icon
                      class="session-delete"
                      icon="mdi:delete-outline"
                      @click=${(e5) => this._deleteSession(s6.id, e5)}
                      title="Delete"
                    ></ha-icon>
                  </div>
                `
    )}
        </div>
      </div>

      <div class="main">
        <div class="header">
          <div class="header-top">
            <ha-icon-button
              title="Conversations"
              @click=${() => this._showSidebar = !this._showSidebar}
            >
              <ha-icon icon="mdi:menu"></ha-icon>
            </ha-icon-button>
            <img src="/api/selora_ai/logo.png" alt="Selora" style="width:28px;height:28px;border-radius:6px;">
            Selora AI
          </div>
          <div class="tabs">
            <div class="tab ${this._activeTab === "chat" ? "active" : ""}" @click=${() => {
      this._activeTab = "chat";
      this._showSidebar = true;
    }}>Chat</div>
            <div class="tab ${this._activeTab === "automations" ? "active" : ""}" @click=${() => {
      this._activeTab = "automations";
      this._showSidebar = false;
      this._loadAutomations();
    }}>Automations</div>
            <div class="tab ${this._activeTab === "settings" ? "active" : ""}" @click=${() => {
      this._activeTab = "settings";
      this._showSidebar = false;
      this._loadConfig();
    }}>Settings</div>
          </div>
        </div>

        ${this._activeTab === "chat" ? this._renderChat() : ""}
        ${this._activeTab === "automations" ? this._renderAutomations() : ""}
        ${this._activeTab === "settings" ? this._renderSettings() : ""}
      </div>

      ${this._renderHardDeleteDialog()}

      ${this._toast ? x`
            <div class="toast ${this._toastType}">
              <span>${this._toast}</span>
              <ha-icon class="toast-close" icon="mdi:close" @click=${() => this._dismissToast()}></ha-icon>
            </div>
          ` : ""}
    `;
  }
  // -------------------------------------------------------------------------
  // Chat pane
  // -------------------------------------------------------------------------
  _renderChat() {
    return x`
      <div class="chat-pane">
        <div class="chat-messages" id="chat-messages">
          ${!this._activeSessionId || this._messages.length === 0 ? i4(this._welcomeKey || 0, x`
                <div class="empty-state welcome" style="max-width:520px;margin:0 auto;text-align:center;">
                  <img src="/api/selora_ai/logo.png" alt="Selora AI" style="width:56px;height:56px;border-radius:12px;margin-bottom:8px;">
                  <div style="font-size:20px;font-weight:600;margin-bottom:4px;">Welcome to Selora AI</div>
                  <div style="font-size:13px;opacity:0.7;margin-bottom:20px;line-height:1.5;">
                    Your intelligent home automation architect. I analyze your devices,
                    detect patterns, and help you build automations using natural language.
                  </div>
                  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;text-align:left;margin-bottom:20px;">
                    <div class="welcome-card" style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                      @click=${() => this._quickStart("Create an automation for my home")}>
                      <ha-icon icon="mdi:lightning-bolt" style="--mdc-icon-size:18px;color:#f59e0b;flex-shrink:0;margin-top:1px;"></ha-icon>
                      <div>
                        <div style="font-size:12px;font-weight:600;">Create Automations</div>
                        <div style="font-size:11px;opacity:0.6;">Describe what you want in plain English</div>
                      </div>
                    </div>
                    <div class="welcome-card" style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                      @click=${() => this._quickStart("Analyze my device usage patterns and suggest automations")}>
                      <ha-icon icon="mdi:magnify-scan" style="--mdc-icon-size:18px;color:#3b82f6;flex-shrink:0;margin-top:1px;"></ha-icon>
                      <div>
                        <div style="font-size:12px;font-weight:600;">Detect Patterns</div>
                        <div style="font-size:11px;opacity:0.6;">AI spots your routines and suggests automations</div>
                      </div>
                    </div>
                    <div class="welcome-card" style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                      @click=${() => this._quickStart("What devices do I have and how are they organized?")}>
                      <ha-icon icon="mdi:home-search-outline" style="--mdc-icon-size:18px;color:#22c55e;flex-shrink:0;margin-top:1px;"></ha-icon>
                      <div>
                        <div style="font-size:12px;font-weight:600;">Manage Devices</div>
                        <div style="font-size:11px;opacity:0.6;">Discover, organize, and control your smart home</div>
                      </div>
                    </div>
                    <div class="welcome-card" style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                      @click=${() => this._quickStart("What can you help me with?")}>
                      <ha-icon icon="mdi:chat-question-outline" style="--mdc-icon-size:18px;color:#a855f7;flex-shrink:0;margin-top:1px;"></ha-icon>
                      <div>
                        <div style="font-size:12px;font-weight:600;">Ask Anything</div>
                        <div style="font-size:11px;opacity:0.6;">Get answers about your home setup</div>
                      </div>
                    </div>
                  </div>
                  <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;opacity:0.4;margin-bottom:10px;">Quick start</div>
                  <div style="display:flex;flex-direction:column;gap:8px;width:100%;">
                    <button class="btn btn-outline" style="width:100%;justify-content:flex-start;gap:8px;padding:10px 14px;font-size:13px;"
                      @click=${() => this._quickStart("Create an automation that turns off all lights at midnight")}>
                      <ha-icon icon="mdi:lightbulb-off-outline" style="--mdc-icon-size:16px;"></ha-icon>
                      Turn off all lights at midnight
                    </button>
                    <button class="btn btn-outline" style="width:100%;justify-content:flex-start;gap:8px;padding:10px 14px;font-size:13px;"
                      @click=${() => this._quickStart("What devices do I have and which ones are currently on?")}>
                      <ha-icon icon="mdi:devices" style="--mdc-icon-size:16px;"></ha-icon>
                      What devices do I have?
                    </button>
                    <button class="btn btn-outline" style="width:100%;justify-content:flex-start;gap:8px;padding:10px 14px;font-size:13px;"
                      @click=${() => this._quickStart("Suggest useful automations based on my devices and usage patterns")}>
                      <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size:16px;"></ha-icon>
                      Suggest automations for my home
                    </button>
                  </div>
                </div>
              `) : this._messages.map((msg, idx) => this._renderMessage(msg, idx))}

          ${this._loading ? x`
                <div class="typing-bubble">
                  <div class="typing-dot"></div>
                  <div class="typing-dot"></div>
                  <div class="typing-dot"></div>
                </div>
              ` : ""}
        </div>

        <div class="chat-input">
          <ha-textfield
            .value=${this._input}
            @input=${(e5) => this._input = e5.target.value}
            @keydown=${(e5) => e5.key === "Enter" && !e5.shiftKey && this._sendMessage()}
            placeholder="Describe an automation or ask a question…"
            ?disabled=${this._loading}
            style="flex:1;"
          ></ha-textfield>
          <ha-icon-button
            @click=${this._sendMessage}
            ?disabled=${this._loading || !this._input.trim()}
            title="Send"
          >
            <ha-icon icon="mdi:send"></ha-icon>
          </ha-icon-button>
        </div>
      </div>
    `;
  }
  _renderMessage(msg, idx) {
    const isUser = msg.role === "user";
    if (msg._streaming && !msg.content)
      return x``;
    return x`
      <div class="message-row">
        <div class="bubble ${isUser ? "user" : "assistant"}">
          <span class="msg-content ${msg._streaming ? "streaming-cursor" : ""}" .innerHTML=${isUser ? msg.content : renderMarkdown(msg.content)}></span>
          ${msg.config_issue ? x`
                <div style="margin-top: 10px;">
                  <mwc-button dense raised @click=${this._goToSettings}>Go to Settings</mwc-button>
                </div>
              ` : ""}
          ${msg.automation ? this._renderProposalCard(msg, idx) : ""}
        </div>
        <div class="bubble-meta">${isUser ? "You" : "Selora AI"} · ${this._formatTime(msg.timestamp)}</div>
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // YAML editor
  // -------------------------------------------------------------------------
  /**
   * Render an editable YAML textarea with an optional save button.
   * @param {string} key         - unique key for tracking edits (_editedYaml)
   * @param {string} originalYaml - the original YAML string to initialise from
   * @param {Function|null} onSave - called with (key) when "Save changes" is clicked; null = no save button (caller handles via accept)
   */
  _renderYamlEditor(key, originalYaml, onSave = null) {
    this._initYamlEdit(key, originalYaml);
    const current = this._editedYaml[key] ?? originalYaml;
    const isDirty = current !== originalYaml;
    const saving = !!this._savingYaml[key];
    return x`
      <textarea
        class="yaml-editor"
        .value=${current}
        @input=${(e5) => {
      this._onYamlInput(key, e5.target.value);
      e5.target.style.height = "auto";
      e5.target.style.height = e5.target.scrollHeight + "px";
    }}
        spellcheck="false"
        autocomplete="off"
        rows="${Math.max(8, (current || "").split("\n").length + 1)}"
      ></textarea>
      ${isDirty || onSave ? x`
        <div class="yaml-edit-bar">
          ${isDirty ? x`
            <span class="yaml-unsaved">
              <ha-icon icon="mdi:circle-edit-outline" style="--mdc-icon-size:13px;"></ha-icon>
              Unsaved changes
            </span>
          ` : x`<span style="flex:1;"></span>`}
          ${onSave ? x`
            <button class="btn btn-primary" ?disabled=${saving || !isDirty} @click=${() => onSave(key)}>
              <ha-icon icon="mdi:content-save" style="--mdc-icon-size:13px;"></ha-icon>
              ${saving ? "Saving\u2026" : "Save changes"}
            </button>
          ` : ""}
        </div>
      ` : ""}
    `;
  }
  // -------------------------------------------------------------------------
  // Automation flowchart helpers
  // -------------------------------------------------------------------------
  /** Strip the domain prefix and underscores: "light.kitchen_lamp" → "kitchen lamp" */
  _fmtEntity(id) {
    if (!id)
      return "";
    const parts = String(id).split(".");
    const raw = (parts.length > 1 ? parts.slice(1).join(".") : parts[0]).replace(/_/g, " ");
    return raw.replace(/\b\w/g, (c3) => c3.toUpperCase());
  }
  _fmtEntities(val) {
    if (!val)
      return "";
    const arr = Array.isArray(val) ? val : [val];
    if (arr.length === 1)
      return this._fmtEntity(arr[0]);
    if (arr.length === 2)
      return `${this._fmtEntity(arr[0])} and ${this._fmtEntity(arr[1])}`;
    return arr.slice(0, -1).map((e5) => this._fmtEntity(e5)).join(", ") + ", and " + this._fmtEntity(arr[arr.length - 1]);
  }
  _fmtState(state) {
    if (state == null)
      return null;
    const s6 = String(state);
    const friendly = { on: "on", off: "off", home: "home", not_home: "away", open: "open", closed: "closed", locked: "locked", unlocked: "unlocked", playing: "playing", paused: "paused", idle: "idle", unavailable: "unavailable", unknown: "unknown" };
    return friendly[s6] || s6.replace(/_/g, " ");
  }
  _describeFlowItem(item) {
    if (!item || typeof item !== "object")
      return String(item ?? "");
    const p2 = item.platform || item.trigger;
    if (p2 === "time") {
      const at = Array.isArray(item.at) ? item.at.join(", ") : item.at;
      return `Every day at ${at}`;
    }
    if (p2 === "sun") {
      const ev = item.event === "sunset" ? "At sunset" : item.event === "sunrise" ? "At sunrise" : `Sun ${(item.event || "").replace(/_/g, " ")}`;
      const offset = item.offset ? ` (${item.offset})` : "";
      return `${ev}${offset}`;
    }
    if (p2 === "state") {
      const eid = this._fmtEntities(item.entity_id);
      const fromState = this._fmtState(item.from);
      const toState = this._fmtState(item.to);
      const dur = item.for ? ` for ${typeof item.for === "object" ? [item.for.hours && `${item.for.hours}h`, item.for.minutes && `${item.for.minutes}m`, item.for.seconds && `${item.for.seconds}s`].filter(Boolean).join(" ") || item.for : item.for}` : "";
      if (toState === "on")
        return `When ${eid} turns on${dur}`;
      if (toState === "off")
        return `When ${eid} turns off${dur}`;
      if (toState && fromState)
        return `When ${eid} changes from ${fromState} to ${toState}${dur}`;
      if (toState)
        return `When ${eid} becomes ${toState}${dur}`;
      return `When ${eid} changes state${dur}`;
    }
    if (p2 === "numeric_state") {
      const eid = this._fmtEntities(item.entity_id);
      if (item.above != null && item.below != null)
        return `When ${eid} is between ${item.above} and ${item.below}`;
      if (item.above != null)
        return `When ${eid} rises above ${item.above}`;
      if (item.below != null)
        return `When ${eid} drops below ${item.below}`;
      return `When ${eid} value changes`;
    }
    if (p2 === "homeassistant") {
      const ev = item.event === "start" ? "starts up" : item.event === "shutdown" ? "shuts down" : item.event || "event";
      return `Home Assistant ${ev}`;
    }
    if (p2 === "time_pattern") {
      if (item.seconds != null)
        return `Every ${item.seconds} seconds`;
      if (item.minutes != null)
        return `Every ${item.minutes} minutes`;
      if (item.hours != null)
        return `Every ${item.hours} hours`;
      return "On a time pattern";
    }
    if (p2 === "template")
      return "When a template becomes true";
    if (p2 === "event")
      return item.event_type ? `Event: ${String(item.event_type).replace(/_/g, " ")}` : "On an event";
    if (p2 === "device")
      return item.type ? String(item.type).replace(/_/g, " ") : "Device trigger";
    if (p2 === "zone") {
      const eid = this._fmtEntities(item.entity_id);
      const zone = this._fmtEntity(item.zone);
      const ev = (item.event || "enters").replace(/_/g, " ");
      return `${eid} ${ev} ${zone}`.trim();
    }
    if (p2 === "mqtt")
      return `MQTT: ${item.topic || "message received"}`;
    if (p2 === "webhook")
      return "Webhook received";
    if (p2 === "tag")
      return `Tag scanned${item.tag_id ? `: ${item.tag_id}` : ""}`;
    if (p2 === "geo_location")
      return "Geo-location event";
    if (p2 === "calendar")
      return `Calendar: ${item.event || "event"}`;
    if (p2)
      return String(p2).replace(/_/g, " ");
    const cond = item.condition;
    if (cond === "state") {
      const eid = this._fmtEntities(item.entity_id);
      const st = this._fmtState(item.state ?? item.to);
      return `${eid} is ${st}`;
    }
    if (cond === "numeric_state") {
      const eid = this._fmtEntities(item.entity_id);
      if (item.above != null && item.below != null)
        return `${eid} between ${item.above} and ${item.below}`;
      if (item.above != null)
        return `${eid} above ${item.above}`;
      if (item.below != null)
        return `${eid} below ${item.below}`;
      return `${eid} numeric check`;
    }
    if (cond === "time") {
      const parts = [];
      if (item.after)
        parts.push(`after ${item.after}`);
      if (item.before)
        parts.push(`before ${item.before}`);
      if (item.weekday) {
        const days = Array.isArray(item.weekday) ? item.weekday.join(", ") : item.weekday;
        parts.push(`on ${days}`);
      }
      return parts.length ? parts.join(" \xB7 ") : "Time window";
    }
    if (cond === "template")
      return "Template evaluates to true";
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
    if (cond === "not")
      return "None of the conditions are true";
    if (cond === "zone") {
      const eid = this._fmtEntities(item.entity_id);
      return `${eid} is in ${this._fmtEntity(item.zone) || "zone"}`;
    }
    if (cond === "device")
      return item.type ? String(item.type).replace(/_/g, " ") : "Device condition";
    if (cond)
      return String(cond).replace(/_/g, " ");
    const svc = item.service || item.action;
    if (svc) {
      const [domain = "", svcName = svc] = String(svc).split(".");
      const friendlyActions = { turn_on: "Turn on", turn_off: "Turn off", toggle: "Toggle", lock: "Lock", unlock: "Unlock", open_cover: "Open", close_cover: "Close", set_temperature: "Set temperature for", set_value: "Set value for", send_command: "Send command to", notify: "Send notification via", reload: "Reload" };
      const name = friendlyActions[svcName] || svcName.replace(/_/g, " ").replace(/\b\w/g, (c3) => c3.toUpperCase());
      const targets = item.target?.entity_id ?? item.data?.entity_id;
      const t3 = this._fmtEntities(targets);
      const extras = [];
      if (item.data?.brightness_pct != null)
        extras.push(`at ${item.data.brightness_pct}% brightness`);
      if (item.data?.temperature != null)
        extras.push(`to ${item.data.temperature}\xB0`);
      if (item.data?.color_temp != null)
        extras.push(`color temp ${item.data.color_temp}`);
      if (item.data?.message)
        extras.push(`"${item.data.message}"`);
      if (item.data?.title)
        extras.push(item.data.title);
      const detail = extras.length ? ` (${extras.join(", ")})` : "";
      return t3 ? `${name} ${t3}${detail}` : `${name}${detail}`;
    }
    if (item.delay) {
      const d3 = item.delay;
      if (typeof d3 === "string")
        return `Wait ${d3}`;
      const parts = [];
      if (d3.hours)
        parts.push(`${d3.hours}h`);
      if (d3.minutes)
        parts.push(`${d3.minutes}m`);
      if (d3.seconds)
        parts.push(`${d3.seconds}s`);
      return parts.length ? `Wait ${parts.join(" ")}` : "Wait";
    }
    if (item.wait_template)
      return "Wait until condition is met";
    if (item.wait_for_trigger)
      return "Wait for a trigger";
    if (item.scene)
      return `Activate scene: ${this._fmtEntity(item.scene)}`;
    if (item.choose)
      return `Choose between ${item.choose.length} option${item.choose.length !== 1 ? "s" : ""}`;
    if (item.repeat) {
      const r4 = item.repeat;
      if (r4.count != null)
        return `Repeat ${r4.count} time${r4.count !== 1 ? "s" : ""}`;
      if (r4.while)
        return "Repeat while condition holds";
      if (r4.until)
        return "Repeat until condition is met";
      return "Repeat";
    }
    if (item.parallel)
      return `Run ${(item.parallel || []).length} actions in parallel`;
    if (item.sequence)
      return `Run a sequence of ${(item.sequence || []).length} steps`;
    if (item.variables)
      return "Set variables";
    if (item.stop)
      return `Stop: ${item.stop}`;
    if (item.event)
      return `Fire event: ${String(item.event).replace(/_/g, " ")}`;
    const SKIP = /* @__PURE__ */ new Set(["id", "enabled", "mode", "alias", "description"]);
    const readable = Object.entries(item).filter(([k2, v2]) => !SKIP.has(k2) && v2 != null && v2 !== "").map(([k2, v2]) => {
      const label = k2.replace(/_/g, " ");
      const val = Array.isArray(v2) ? v2.map((x2) => typeof x2 === "object" ? "\u2026" : x2).join(", ") : v2;
      return `${label}: ${val}`;
    }).slice(0, 3);
    return readable.length ? readable.join(" \xB7 ") : "Step";
  }
  _renderAutomationFlowchart(auto) {
    if (!auto)
      return x``;
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
    if (!triggers.length && !actions.length)
      return x``;
    return x`
      <div class="flow-chart">
        <div class="flow-section">
          <div class="flow-label">Trigger</div>
          ${triggers.map((t3) => x`<div class="flow-node trigger-node">${this._describeFlowItem(t3)}</div>`)}
        </div>
        ${conditions.length ? x`
          <div class="flow-arrow">↓</div>
          <div class="flow-section">
            <div class="flow-label">Condition</div>
            ${conditions.map((c3) => x`<div class="flow-node condition-node">${this._describeFlowItem(c3)}</div>`)}
          </div>
        ` : ""}
        <div class="flow-arrow">↓</div>
        <div class="flow-section">
          <div class="flow-label">Actions</div>
          ${actions.map((a4, i5) => x`
            ${i5 > 0 ? x`<div class="flow-arrow-sm">↓</div>` : ""}
            <div class="flow-node action-node">${this._describeFlowItem(a4)}</div>
          `)}
        </div>
      </div>
    `;
  }
  _renderProposalCard(msg, msgIndex) {
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
              <ha-icon icon="mdi:check"></ha-icon> Saved to your system (disabled — enable in Automations)
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
            <div class="proposal-status declined">Dismissed. You can refine it by replying below.</div>
          </div>
        </div>
      `;
    }
    if (status === "refining") {
      return x`
        <div class="proposal-card" style="margin-top:12px; opacity:0.75;">
          <div class="proposal-header" style="color:var(--warning-color, #ff9800);">
            <ha-icon icon="mdi:pencil-circle-outline"></ha-icon>
            Being Refined
          </div>
          <div class="proposal-body">
            <div class="proposal-name">${automation.alias}</div>
            <div class="proposal-status" style="background:rgba(255,152,0,0.1); color:var(--warning-color,#ff9800);">
              <ha-icon icon="mdi:arrow-down"></ha-icon>
              Refinement requested — see the updated proposal below.
            </div>
          </div>
        </div>
      `;
    }
    const yamlOpen = this._yamlOpen && this._yamlOpen[msgIndex];
    const yamlKey = `proposal_${msgIndex}`;
    const hasEdits = this._editedYaml[yamlKey] !== void 0 && this._editedYaml[yamlKey] !== yaml;
    return x`
      <div class="proposal-card">
        <div class="proposal-header">
          <ha-icon icon="mdi:lightning-bolt"></ha-icon>
          Automation Proposal
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          ${scrutinyTags.length ? x`
                <div style="display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 4px;">
                  ${scrutinyTags.map(
      (tag) => x`<div class="chip" style="background:rgba(33,150,243,0.10); color:var(--primary-color); border:1px solid rgba(33,150,243,0.18);">${tag}</div>`
    )}
                </div>
              ` : ""}

          ${msg.description ? x`
                <div class="proposal-description-label">What this automation does</div>
                <div class="proposal-description">${msg.description}</div>
              ` : ""}

          ${risk?.level === "elevated" ? x`
                <div class="proposal-status" style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25);">
                  <ha-icon icon="mdi:alert-outline"></ha-icon>
                  <div>
                    <strong>Elevated risk review recommended.</strong>
                    <div style="margin-top:4px;">${risk.summary}</div>
                    ${risk.reasons?.length ? x`<div style="margin-top:6px; font-size:12px;">${risk.reasons.join(" ")}</div>` : ""}
                  </div>
                </div>
              ` : ""}

          ${this._renderAutomationFlowchart(automation)}

          <div class="yaml-toggle" @click=${() => this._toggleYaml(msgIndex)}>
            <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:14px;"></ha-icon>
            ${yamlOpen ? "Hide YAML" : "Edit YAML"}
          </div>
          ${yamlOpen ? this._renderYamlEditor(yamlKey, yaml) : ""}

          <div class="proposal-verify">
            ${hasEdits ? "Your YAML edits will be used when you accept." : "Does the flow above match what you intended?"}
          </div>

          <div class="proposal-actions">
            <button class="btn btn-success" @click=${() => this._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}>
              <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
              Accept &amp; Save
            </button>
            <button class="btn btn-outline" @click=${() => this._refineAutomation(msgIndex, automation, msg.description)}>
              <ha-icon icon="mdi:pencil" style="--mdc-icon-size:14px;"></ha-icon>
              Refine
            </button>
            <button class="btn btn-danger" @click=${() => this._declineAutomation(msgIndex)}>
              <ha-icon icon="mdi:close" style="--mdc-icon-size:14px;"></ha-icon>
              Decline
            </button>
          </div>
        </div>
      </div>
    `;
  }
  _toggleYaml(msgIndex) {
    this._yamlOpen = { ...this._yamlOpen || {}, [msgIndex]: !(this._yamlOpen || {})[msgIndex] };
    this.requestUpdate();
  }
  // -------------------------------------------------------------------------
  // Automations tab
  // -------------------------------------------------------------------------
  _toggleExpandAutomation(key) {
    this._expandedAutomations = { ...this._expandedAutomations, [key]: !this._expandedAutomations[key] };
    this.requestUpdate();
  }
  _getSelectedAutomationIds() {
    return Object.keys(this._selectedAutomationIds || {}).filter((id) => this._selectedAutomationIds[id]);
  }
  _automationIsEnabled(automation) {
    if (!automation)
      return false;
    if (automation.state === "on")
      return true;
    if (automation.state === "off")
      return false;
    if (automation.state === "unavailable" && typeof automation.persisted_enabled === "boolean") {
      return automation.persisted_enabled;
    }
    return false;
  }
  _toggleAutomationSelection(automationId, evt) {
    evt.stopPropagation();
    if (!automationId)
      return;
    const checked = !!evt.target.checked;
    this._selectedAutomationIds = {
      ...this._selectedAutomationIds,
      [automationId]: checked
    };
    this.requestUpdate();
  }
  _toggleSelectAllFiltered(filteredAutomations, checked) {
    const selectable = (filteredAutomations || []).filter((a4) => !a4._draft && a4.automation_id);
    const next = { ...this._selectedAutomationIds };
    for (const auto of selectable) {
      next[auto.automation_id] = checked;
    }
    this._selectedAutomationIds = next;
    this.requestUpdate();
  }
  _clearAutomationSelection() {
    this._selectedAutomationIds = {};
    this.requestUpdate();
  }
  async _bulkToggleSelected(enable) {
    if (this._bulkActionInProgress)
      return;
    const selectedIds = this._getSelectedAutomationIds();
    if (!selectedIds.length)
      return;
    const byId = new Map(this._automations.map((a4) => [a4.automation_id, a4]));
    const targets = selectedIds.map((id) => byId.get(id)).filter((a4) => a4 && !a4._draft && a4.automation_id).filter((a4) => enable ? !this._automationIsEnabled(a4) : this._automationIsEnabled(a4));
    const skippedCount = selectedIds.length - targets.length;
    if (!targets.length) {
      this._showToast(`Selected automations are already ${enable ? "enabled" : "disabled"}.`, "info");
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
            enabled: enable
          });
          successCount += 1;
        } catch (err) {
          console.error("Bulk toggle failed", auto.automation_id, err);
        }
      }
      await this._loadAutomations();
      const failedCount = targets.length - successCount;
      if (failedCount === 0) {
        const skippedNote = skippedCount > 0 ? ` (${skippedCount} already in target state)` : "";
        this._showToast(`${enable ? "Enabled" : "Disabled"} ${successCount} automation(s)${skippedNote}.`, "success");
      } else {
        this._showToast(`${enable ? "Enable" : "Disable"} completed: ${successCount} succeeded, ${failedCount} failed.`, "error");
      }
    } finally {
      this._bulkActionInProgress = false;
      this._bulkActionLabel = "";
      this.requestUpdate();
    }
  }
  async _bulkSoftDeleteSelected() {
    if (this._bulkActionInProgress)
      return;
    const selectedIds = this._getSelectedAutomationIds();
    if (!selectedIds.length)
      return;
    const byId = new Map(this._automations.map((a4) => [a4.automation_id, a4]));
    const targets = selectedIds.map((id) => byId.get(id)).filter((a4) => a4 && !a4._draft && a4.automation_id);
    if (!targets.length)
      return;
    if (!confirm(`Soft-delete ${targets.length} selected automation(s)?`))
      return;
    this._bulkActionInProgress = true;
    this._bulkActionLabel = `Soft-deleting ${targets.length} automation(s)\u2026`;
    let successCount = 0;
    try {
      for (const auto of targets) {
        try {
          await this.hass.callWS({
            type: "selora_ai/soft_delete_automation",
            automation_id: auto.automation_id
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
        this._showToast(`Soft-delete completed: ${successCount} succeeded, ${failedCount} failed.`, "error");
      }
    } finally {
      this._bulkActionInProgress = false;
      this._bulkActionLabel = "";
      this.requestUpdate();
    }
  }
  async _toggleAutomation(entityId, automationId, enabled) {
    try {
      await this.hass.callWS({
        type: "selora_ai/toggle_automation",
        automation_id: automationId,
        entity_id: entityId,
        enabled: !!enabled
      });
      await this._loadAutomations();
    } catch (err) {
      console.error("Failed to toggle automation", err);
      const message = err?.message || "unknown error";
      this._showToast(`Failed to toggle automation: ${message}`, "error");
    }
  }
  _toggleBurgerMenu(automationId, evt) {
    evt.stopPropagation();
    this._openBurgerMenu = this._openBurgerMenu === automationId ? null : automationId;
    this.requestUpdate();
  }
  _closeBurgerMenus() {
    if (this._openBurgerMenu) {
      this._openBurgerMenu = null;
      this.requestUpdate();
    }
  }
  // -------------------------------------------------------------------------
  // Version history methods
  // -------------------------------------------------------------------------
  async _openVersionHistory(automationId) {
    const isOpen = !!this._versionHistoryOpen[automationId];
    this._versionHistoryOpen = { ...this._versionHistoryOpen, [automationId]: !isOpen };
    if (!isOpen && !this._versions[automationId]) {
      await this._loadVersionHistory(automationId);
    }
    this.requestUpdate();
  }
  async _loadVersionHistory(automationId) {
    this._loadingVersions = { ...this._loadingVersions, [automationId]: true };
    try {
      const result = await this.hass.callWS({ type: "selora_ai/get_automation_versions", automation_id: automationId });
      const ordered = Array.isArray(result) ? [...result].reverse() : [];
      this._versions = { ...this._versions, [automationId]: ordered };
    } catch (err) {
      console.error("Failed to load version history", err);
      this._showToast("Failed to load version history: " + err.message, "error");
    } finally {
      this._loadingVersions = { ...this._loadingVersions, [automationId]: false };
    }
    this.requestUpdate();
  }
  async _openDiffViewer(automationId) {
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
  async _loadDiff(automationId, versionAId, versionBId) {
    if (!versionAId || !versionBId)
      return;
    this._loadingDiff = true;
    this._diffResult = [];
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_automation_diff",
        automation_id: automationId,
        version_id_a: versionAId,
        version_id_b: versionBId
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
  async _restoreVersion(automationId, versionId, yamlText) {
    const key = `${automationId}_${versionId}`;
    this._restoringVersion = { ...this._restoringVersion, [key]: true };
    try {
      await this.hass.callWS({
        type: "selora_ai/update_automation_yaml",
        automation_id: automationId,
        yaml_text: yamlText,
        version_message: `Restored from version ${versionId}`
      });
      this._versionHistoryOpen = { ...this._versionHistoryOpen, [automationId]: false };
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
  // -------------------------------------------------------------------------
  // Soft delete / restore methods
  // -------------------------------------------------------------------------
  async _softDeleteAutomation(automationId) {
    this._deletingAutomation = { ...this._deletingAutomation, [automationId]: true };
    try {
      await this.hass.callWS({ type: "selora_ai/soft_delete_automation", automation_id: automationId });
      await this._loadAutomations();
      this._showToast("Automation moved to Recently Deleted.", "success");
    } catch (err) {
      console.error("Failed to delete automation", err);
      this._showToast("Failed to delete automation: " + err.message, "error");
    } finally {
      this._deletingAutomation = { ...this._deletingAutomation, [automationId]: false };
    }
    this.requestUpdate();
  }
  async _restoreDeletedAutomation(automationId) {
    this._restoringAutomation = { ...this._restoringAutomation, [automationId]: true };
    try {
      await this.hass.callWS({ type: "selora_ai/restore_automation", automation_id: automationId });
      await this._loadDeletedAutomations();
      await this._loadAutomations();
      this._showToast("Automation restored.", "success");
    } catch (err) {
      console.error("Failed to restore automation", err);
      this._showToast("Failed to restore automation: " + err.message, "error");
    } finally {
      this._restoringAutomation = { ...this._restoringAutomation, [automationId]: false };
    }
    this.requestUpdate();
  }
  _openHardDeleteDialog(automationId, alias) {
    this._hardDeleteTarget = { automationId, alias };
    this._hardDeleteAliasInput = "";
    this.requestUpdate();
  }
  _closeHardDeleteDialog() {
    this._hardDeleteTarget = null;
    this._hardDeleteAliasInput = "";
    this.requestUpdate();
  }
  async _confirmHardDelete() {
    const target = this._hardDeleteTarget;
    if (!target)
      return;
    const { automationId, alias } = target;
    if (this._hardDeleteAliasInput !== alias)
      return;
    this._hardDeletingAutomation = { ...this._hardDeletingAutomation, [automationId]: true };
    try {
      await this.hass.callWS({ type: "selora_ai/hard_delete_automation", automation_id: automationId });
      this._closeHardDeleteDialog();
      await this._loadDeletedAutomations();
      await this._loadAutomations();
      this._showToast("Automation permanently deleted.", "success");
    } catch (err) {
      console.error("Failed to hard delete automation", err);
      this._showToast("Failed to permanently delete automation: " + err.message, "error");
    } finally {
      this._hardDeletingAutomation = { ...this._hardDeletingAutomation, [automationId]: false };
    }
    this.requestUpdate();
  }
  async _toggleDeletedSection() {
    this._showDeleted = !this._showDeleted;
    if (this._showDeleted && this._deletedAutomations.length === 0) {
      await this._loadDeletedAutomations();
    }
    this.requestUpdate();
  }
  async _loadDeletedAutomations() {
    this._loadingDeleted = true;
    try {
      const result = await this.hass.callWS({ type: "selora_ai/get_automations", include_deleted: true });
      this._deletedAutomations = (result || []).filter((a4) => a4.is_deleted);
    } catch (err) {
      console.error("Failed to load deleted automations", err);
      this._showToast("Failed to load deleted automations: " + err.message, "error");
    } finally {
      this._loadingDeleted = false;
    }
    this.requestUpdate();
  }
  // -------------------------------------------------------------------------
  // Refine in chat
  // -------------------------------------------------------------------------
  async _loadAutomationToChat(automationId) {
    if (!automationId) {
      this._showToast("This automation cannot be refined because it has no automation ID.", "error");
      return;
    }
    this._loadingToChat = { ...this._loadingToChat, [automationId]: true };
    try {
      const result = await this.hass.callWS({ type: "selora_ai/load_automation_to_session", automation_id: automationId });
      const sessionId = result?.session_id;
      if (sessionId) {
        this._activeSessionId = sessionId;
        this._activeTab = "chat";
        await this._openSession(sessionId);
        this._showToast("Automation loaded into chat.", "success");
      }
    } catch (err) {
      console.error("Failed to load automation to chat", err);
      this._showToast("Failed to load automation into chat: " + err.message, "error");
    } finally {
      this._loadingToChat = { ...this._loadingToChat, [automationId]: false };
    }
    this.requestUpdate();
  }
  // -------------------------------------------------------------------------
  // Render: version history drawer
  // -------------------------------------------------------------------------
  _renderVersionHistoryDrawer(a4) {
    const automationId = a4.automation_id || a4.entity_id;
    const versions = this._versions[automationId] || [];
    const loading = this._loadingVersions[automationId];
    const activeTab = this._versionTab[automationId] || "versions";
    const lineage = this._lineage[automationId] || [];
    const loadingLineage = this._loadingLineage[automationId];
    const switchTab = async (tab) => {
      this._versionTab = { ...this._versionTab, [automationId]: tab };
      if (tab === "lineage" && !this._lineage[automationId]) {
        await this._loadLineage(automationId);
      }
      this.requestUpdate();
    };
    return x`
      <div style="border:1px solid var(--divider-color);border-radius:8px;margin:8px 0 4px;padding:12px;background:var(--secondary-background-color);">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
          <div style="display:flex;gap:0;">
            <button class="tab ${activeTab === "versions" ? "active" : ""}"
              style="font-size:12px;padding:4px 12px;border-radius:6px 0 0 6px;border:1px solid var(--divider-color);background:${activeTab === "versions" ? "var(--primary-color)" : "transparent"};color:${activeTab === "versions" ? "#fff" : "inherit"};cursor:pointer;"
              @click=${() => switchTab("versions")}>
              <ha-icon icon="mdi:history" style="--mdc-icon-size:13px;vertical-align:middle;margin-right:3px;"></ha-icon>
              Versions
            </button>
            <button class="tab ${activeTab === "lineage" ? "active" : ""}"
              style="font-size:12px;padding:4px 12px;border-radius:0 6px 6px 0;border:1px solid var(--divider-color);border-left:none;background:${activeTab === "lineage" ? "var(--primary-color)" : "transparent"};color:${activeTab === "lineage" ? "#fff" : "inherit"};cursor:pointer;"
              @click=${() => switchTab("lineage")}>
              <ha-icon icon="mdi:timeline-outline" style="--mdc-icon-size:13px;vertical-align:middle;margin-right:3px;"></ha-icon>
              Lineage
            </button>
          </div>
          ${activeTab === "versions" && versions.length >= 2 ? x`<button class="btn btn-outline" style="font-size:11px;padding:3px 8px;"
                @click=${() => this._openDiffViewer(automationId)}>
                <ha-icon icon="mdi:compare" style="--mdc-icon-size:12px;"></ha-icon>
                Compare versions
              </button>` : ""}
        </div>
        ${activeTab === "lineage" ? loadingLineage ? x`<div style="opacity:0.5;font-size:12px;padding:8px 0;">Loading lineage…</div>` : lineage.length === 0 ? x`<div style="opacity:0.5;font-size:12px;padding:8px 0;">No lineage entries yet. Lineage is recorded as automations are created and refined via chat.</div>` : x`<div style="position:relative;padding-left:20px;margin-top:4px;">
                  <div style="position:absolute;left:7px;top:0;bottom:0;width:2px;background:var(--divider-color);border-radius:2px;"></div>
                  ${lineage.map((entry, i5) => {
      const date = new Date(entry.timestamp);
      const relativeTime = this._relativeTime(date);
      const actionIcons = { created: "mdi:plus-circle", refined: "mdi:chat-processing-outline", updated: "mdi:pencil", restored: "mdi:restore" };
      const icon = actionIcons[entry.action] || "mdi:circle-small";
      const canJump = !!entry.session_id;
      return x`
                      <div style="position:relative;margin-bottom:12px;padding-left:12px;">
                        <ha-icon icon="${icon}" style="position:absolute;left:-12px;top:1px;--mdc-icon-size:16px;color:var(--primary-color);background:var(--card-background-color);border-radius:50%;"></ha-icon>
                        <div style="display:flex;flex-direction:column;gap:2px;">
                          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                            <span style="font-size:12px;font-weight:600;text-transform:capitalize;">${entry.action}</span>
                            <span style="font-size:11px;opacity:0.6;" title=${date.toISOString()}>${relativeTime}</span>
                            ${i5 === lineage.length - 1 ? x`<span style="font-size:10px;background:var(--primary-color);color:#fff;border-radius:4px;padding:1px 5px;">current</span>` : ""}
                          </div>
                          ${entry.session_title || entry.session_preview ? x`<div style="font-size:11px;opacity:0.7;font-style:italic;">${entry.session_title || entry.session_preview}</div>` : entry.session_id ? x`<div style="font-size:11px;opacity:0.5;">Chat session</div>` : x`<div style="font-size:11px;opacity:0.5;">Manual edit</div>`}
                          ${canJump ? x`<span style="font-size:11px;color:var(--primary-color);cursor:pointer;text-decoration:underline;"
                                @click=${() => {
        this._activeTab = "chat";
        this._activeSessionId = entry.session_id;
        this._openSession(entry.session_id);
        if (entry.message_index != null) {
          setTimeout(() => {
            const msgs = this.shadowRoot?.querySelectorAll(".message-row");
            if (msgs && msgs[entry.message_index])
              msgs[entry.message_index].scrollIntoView({ behavior: "smooth", block: "start" });
          }, 400);
        }
      }}>
                                Jump to session →
                              </span>` : ""}
                        </div>
                      </div>
                    `;
    })}
                </div>` : loading ? x`<div style="opacity:0.5;font-size:12px;">Loading…</div>` : versions.length === 0 ? x`<div style="opacity:0.5;font-size:12px;">No version history yet.</div>` : versions.map((v2, i5) => {
      const key = `${automationId}_${v2.version_id}`;
      const restoring = this._restoringVersion[key];
      const date = new Date(v2.created_at);
      const relativeTime = this._relativeTime(date);
      return x`
                <div style="border-bottom:1px solid var(--divider-color);padding:8px 0;display:flex;flex-direction:column;gap:4px;">
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:11px;font-weight:600;opacity:0.7;">v${versions.length - i5}</span>
                    <span style="font-size:12px;" title=${date.toISOString()}>${relativeTime}</span>
                    ${i5 === 0 ? x`<span style="font-size:10px;background:var(--primary-color);color:#fff;border-radius:4px;padding:1px 5px;">current</span>` : ""}
                    ${v2.session_id ? x`<span style="font-size:11px;opacity:0.6;cursor:pointer;text-decoration:underline;"
                          @click=${() => {
        this._activeSessionId = v2.session_id;
        this._activeTab = "chat";
        this._openSession(v2.session_id);
      }}>
                          from chat session
                        </span>` : ""}
                  </div>
                  ${v2.message || v2.version_message ? x`<div style="font-size:11px;opacity:0.7;font-style:italic;">"${v2.message || v2.version_message}"</div>` : ""}
                  <div style="display:flex;gap:6px;margin-top:4px;flex-wrap:wrap;">
                    <button class="btn btn-outline" style="font-size:11px;padding:3px 8px;"
                      @click=${() => this._toggleExpandAutomation(`ver_${key}`)}>
                      <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:11px;"></ha-icon>
                      ${this._expandedAutomations[`ver_${key}`] ? "Hide YAML" : "View YAML"}
                    </button>
                    <button class="btn btn-outline" style="font-size:11px;padding:3px 8px;"
                      @click=${async () => {
        await this._openDiffViewer(automationId);
        this._diffVersionA = this._versions[automationId]?.[0]?.version_id || null;
        this._diffVersionB = v2.version_id;
        await this._loadDiff(automationId, this._diffVersionA, this._diffVersionB);
        this.requestUpdate();
      }}>
                      <ha-icon icon="mdi:compare" style="--mdc-icon-size:11px;"></ha-icon>
                      Compare with current
                    </button>
                    <button class="btn btn-outline" style="font-size:11px;padding:3px 8px;"
                      ?disabled=${!!this._loadingToChat[automationId]}
                      @click=${() => this._loadAutomationToChat(automationId)}>
                      <ha-icon icon="mdi:chat-processing-outline" style="--mdc-icon-size:11px;"></ha-icon>
                      ${this._loadingToChat[automationId] ? "Loading\u2026" : "Refine in chat"}
                    </button>
                    ${i5 > 0 ? x`<button class="btn btn-outline" style="font-size:11px;padding:3px 8px;"
                          ?disabled=${restoring || !(v2.yaml || v2.yaml_content)}
                          @click=${() => this._restoreVersion(automationId, v2.version_id, v2.yaml || v2.yaml_content || "")}>
                          <ha-icon icon="mdi:restore" style="--mdc-icon-size:11px;"></ha-icon>
                          ${restoring ? "Restoring\u2026" : "Restore this version"}
                        </button>` : ""}
                  </div>
                  ${this._expandedAutomations[`ver_${key}`] ? x`<pre style="font-size:11px;background:#1a1a2e;color:#e0e0e0;padding:8px;border-radius:6px;overflow-x:auto;margin:4px 0 0;white-space:pre-wrap;">${v2.yaml || v2.yaml_content || "(no YAML stored)"}</pre>` : ""}
                </div>
              `;
    })}
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Render: diff viewer modal
  // -------------------------------------------------------------------------
  _renderDiffViewer() {
    if (!this._diffOpen)
      return "";
    const automationId = this._diffAutomationId;
    const versions = this._versions[automationId] || [];
    return x`
      <div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;"
        @click=${(e5) => {
      if (e5.target === e5.currentTarget) {
        this._diffOpen = false;
        this.requestUpdate();
      }
    }}>
        <div style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:760px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.4);">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--divider-color);">
            <span style="font-weight:700;font-size:15px;">
              <ha-icon icon="mdi:compare" style="--mdc-icon-size:17px;vertical-align:middle;margin-right:6px;"></ha-icon>
              Compare Versions
            </span>
            <ha-icon icon="mdi:close" style="cursor:pointer;--mdc-icon-size:20px;" @click=${() => {
      this._diffOpen = false;
      this.requestUpdate();
    }}></ha-icon>
          </div>
          <div style="padding:12px 20px;border-bottom:1px solid var(--divider-color);display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:8px;">
              <span style="font-size:12px;opacity:0.7;">Version A (newer):</span>
              <select style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
                .value=${this._diffVersionA || ""}
                @change=${async (e5) => {
      this._diffVersionA = e5.target.value;
      await this._loadDiff(automationId, this._diffVersionA, this._diffVersionB);
    }}>
                ${versions.map((v2, i5) => x`<option value=${v2.version_id}>v${versions.length - i5} — ${v2.message || v2.version_message || new Date(v2.created_at).toLocaleDateString()}</option>`)}
              </select>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
              <span style="font-size:12px;opacity:0.7;">Version B (older):</span>
              <select style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
                .value=${this._diffVersionB || ""}
                @change=${async (e5) => {
      this._diffVersionB = e5.target.value;
      await this._loadDiff(automationId, this._diffVersionA, this._diffVersionB);
    }}>
                ${versions.map((v2, i5) => x`<option value=${v2.version_id}>v${versions.length - i5} — ${v2.message || v2.version_message || new Date(v2.created_at).toLocaleDateString()}</option>`)}
              </select>
            </div>
          </div>
          <div style="flex:1;overflow-y:auto;padding:12px 20px;">
            ${this._loadingDiff ? x`<div style="opacity:0.5;text-align:center;padding:24px;">Loading diff…</div>` : this._diffResult.length === 0 ? x`<div style="opacity:0.5;text-align:center;padding:24px;">No differences found.</div>` : x`<pre style="font-size:12px;margin:0;font-family:monospace;white-space:pre-wrap;">${this._diffResult.map((line) => {
      const bg = line.startsWith("+") ? "rgba(40,167,69,0.15)" : line.startsWith("-") ? "rgba(220,53,69,0.15)" : "transparent";
      const color = line.startsWith("+") ? "#40c057" : line.startsWith("-") ? "#fa5252" : "var(--primary-text-color)";
      return x`<span style="display:block;background:${bg};color:${color};padding:1px 4px;">${line}</span>`;
    })}</pre>`}
          </div>
        </div>
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Render: recently deleted section
  // -------------------------------------------------------------------------
  _renderDeletedSection() {
    const daysRemaining = (deletedAt) => {
      const elapsed = (Date.now() - new Date(deletedAt).getTime()) / (1e3 * 60 * 60 * 24);
      return Math.max(0, Math.round(30 - elapsed));
    };
    return x`
      <div style="margin-top:16px;">
        <div class="expand-toggle" style="display:flex;align-items:center;gap:6px;"
          @click=${() => this._toggleDeletedSection()}>
          <ha-icon icon="mdi:trash-can-outline" style="--mdc-icon-size:14px;opacity:0.6;"></ha-icon>
          <span>Recently Deleted</span>
          <ha-icon icon="mdi:chevron-${this._showDeleted ? "up" : "down"}" style="--mdc-icon-size:14px;margin-left:auto;"></ha-icon>
        </div>
        ${this._showDeleted ? x`
              <div style="margin-top:8px;">
                ${this._loadingDeleted ? x`<div style="opacity:0.5;font-size:12px;padding:8px 0;">Loading…</div>` : this._deletedAutomations.length === 0 ? x`<div style="opacity:0.45;font-size:12px;padding:8px 0;">No recently deleted automations.</div>` : this._deletedAutomations.map((a4) => {
      const automationId = a4.automation_id || a4.entity_id;
      const days = daysRemaining(a4.deleted_at);
      const restoring = this._restoringAutomation[automationId];
      const hardDeleting = this._hardDeletingAutomation[automationId];
      return x`
                        <div class="card" style="opacity:0.8;border-left:3px solid var(--error-color);">
                          <div class="card-header">
                            <h3 style="flex:1;">${a4.alias}</h3>
                            ${days <= 3 ? x`<span style="font-size:10px;background:var(--error-color);color:#fff;border-radius:4px;padding:2px 6px;">⚠ ${days}d left</span>` : x`<span style="font-size:11px;opacity:0.6;">${days} days until purge</span>`}
                          </div>
                          <p style="font-size:11px;opacity:0.6;margin:4px 0;">
                            Deleted ${this._relativeTime(new Date(a4.deleted_at))}
                          </p>
                          <div class="card-actions">
                            <button class="btn btn-outline" ?disabled=${restoring || hardDeleting}
                              @click=${() => this._restoreDeletedAutomation(automationId)}>
                              <ha-icon icon="mdi:restore" style="--mdc-icon-size:13px;"></ha-icon>
                              ${restoring ? "Restoring\u2026" : "Restore"}
                            </button>
                            <button class="btn btn-outline btn-danger" ?disabled=${restoring || hardDeleting}
                              @click=${() => this._openHardDeleteDialog(automationId, a4.alias)}>
                              <ha-icon icon="mdi:trash-can" style="--mdc-icon-size:13px;"></ha-icon>
                              ${hardDeleting ? "Deleting\u2026" : "Permanently Delete"}
                            </button>
                          </div>
                        </div>
                      `;
    })}
              </div>
            ` : ""}
      </div>
    `;
  }
  _renderHardDeleteDialog() {
    if (!this._hardDeleteTarget)
      return "";
    const { automationId, alias } = this._hardDeleteTarget;
    const hardDeleting = !!this._hardDeletingAutomation[automationId];
    const canConfirm = this._hardDeleteAliasInput === alias;
    return x`
      <div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;"
        @click=${(e5) => {
      if (e5.target === e5.currentTarget && !hardDeleting) {
        this._closeHardDeleteDialog();
      }
    }}>
        <div style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:520px;padding:18px;box-shadow:0 8px 32px rgba(0,0,0,0.4);border:1px solid var(--divider-color);">
          <div style="font-size:16px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:8px;color:var(--error-color);">
            <ha-icon icon="mdi:alert-octagon"></ha-icon>
            Permanently Delete Automation
          </div>
          <p style="font-size:13px;opacity:0.85;margin:0 0 10px;line-height:1.45;">
            This action cannot be undone. Type the automation alias to confirm permanent deletion.
          </p>
          <p style="font-size:12px;opacity:0.75;margin:0 0 8px;">
            Alias: <strong>${alias}</strong>
          </p>
          <ha-textfield
            .value=${this._hardDeleteAliasInput}
            @input=${(e5) => this._hardDeleteAliasInput = e5.target.value}
            placeholder="Type alias exactly"
            ?disabled=${hardDeleting}
            style="width:100%;"
          ></ha-textfield>
          <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:14px;">
            <button class="btn btn-outline" ?disabled=${hardDeleting} @click=${() => this._closeHardDeleteDialog()}>
              Cancel
            </button>
            <button class="btn btn-danger" ?disabled=${hardDeleting || !canConfirm} @click=${() => this._confirmHardDelete()}>
              ${hardDeleting ? "Deleting\u2026" : "Permanently Delete"}
            </button>
          </div>
        </div>
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Helper: relative time
  // -------------------------------------------------------------------------
  _relativeTime(date) {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1e3);
    if (seconds < 60)
      return "just now";
    if (seconds < 3600)
      return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400)
      return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800)
      return `${Math.floor(seconds / 86400)}d ago`;
    return date.toLocaleDateString();
  }
  _renderAutomations() {
    const filterText = (this._automationFilter || "").toLowerCase();
    const filteredAutomations = filterText ? this._automations.filter((a4) => (a4.alias || "").toLowerCase().includes(filterText)) : this._automations;
    const selectableAutomations = filteredAutomations.filter((a4) => !a4._draft && a4.automation_id);
    const selectableIds = selectableAutomations.map((a4) => a4.automation_id);
    const selectedIds = this._getSelectedAutomationIds();
    const selectedVisibleCount = selectableIds.filter((id) => this._selectedAutomationIds[id]).length;
    const allVisibleSelected = selectableIds.length > 0 && selectedVisibleCount === selectableIds.length;
    const partiallyVisibleSelected = selectedVisibleCount > 0 && !allVisibleSelected;
    const hiddenSelectedCount = Math.max(0, selectedIds.length - selectedVisibleCount);
    const bulkDisabled = selectedIds.length === 0 || this._bulkActionInProgress;
    return x`
      <div class="scroll-view" @click=${() => this._closeBurgerMenus()}>
        <div class="sub-tabs">
          <button class="sub-tab ${this._automationsSubTab === "my_automations" ? "active" : ""}"
            @click=${() => {
      this._automationsSubTab = "my_automations";
    }}>
            My Automations
          </button>
          <button class="sub-tab ${this._automationsSubTab === "suggestions" ? "active" : ""}"
            @click=${() => {
      this._automationsSubTab = "suggestions";
    }}>
            Suggestions
            ${(this._suggestions || []).length + (this._proactiveSuggestions || []).length > 0 ? x`<span class="badge">${(this._suggestions || []).length + (this._proactiveSuggestions || []).length}</span>` : ""}
          </button>
        </div>
        ${this._automationsSubTab === "my_automations" ? x`
        ${this._automations.length > 0 ? x`
              <div class="filter-row">
                <div style="display:flex;align-items:center;gap:8px;justify-content:center;">
                  ${this._bulkEditMode ? x`
                    <label class="bulk-select-all">
                      <input type="checkbox"
                        ?checked=${allVisibleSelected}
                        .indeterminate=${partiallyVisibleSelected}
                        ?disabled=${selectableIds.length === 0 || this._bulkActionInProgress}
                        @change=${(e5) => this._toggleSelectAllFiltered(filteredAutomations, e5.target.checked)}>
                      <span>Select all</span>
                    </label>
                  ` : ""}
                  <div class="filter-input-wrap">
                    <ha-icon icon="mdi:magnify"></ha-icon>
                    <input type="text" placeholder="Filter automations…"
                      .value=${this._automationFilter}
                      @input=${(e5) => {
      this._automationFilter = e5.target.value;
    }}>
                  </div>
                  <button class="btn btn-primary" style="white-space:nowrap;" @click=${() => {
      this._newAutoName = "";
      this._showNewAutoDialog = true;
    }}>
                    <ha-icon icon="mdi:plus" style="--mdc-icon-size:14px;"></ha-icon>
                    New Automation
                  </button>
                  ${this._bulkEditMode ? x`
                    <button class="btn btn-outline" style="white-space:nowrap;" @click=${() => {
      this._bulkEditMode = false;
      this._clearAutomationSelection();
    }}>
                      Done
                    </button>
                  ` : x`
                    <button class="btn btn-outline" style="white-space:nowrap;" @click=${() => {
      this._bulkEditMode = true;
    }}>
                      <ha-icon icon="mdi:checkbox-multiple-outline" style="--mdc-icon-size:14px;"></ha-icon>
                      Bulk edit
                    </button>
                  `}
                </div>
              </div>
              ${this._bulkEditMode && selectedIds.length > 0 ? x`
                <div class="bulk-actions-row">
                  <div class="left">
                    ${selectedIds.length} selected${hiddenSelectedCount > 0 ? x` <span style="opacity:0.65;font-weight:500;">(${hiddenSelectedCount} hidden by filter)</span>` : ""}
                    ${this._bulkActionInProgress ? x`<span style="opacity:0.75;font-weight:500;"> · ${this._bulkActionLabel}</span>` : ""}
                  </div>
                  <div class="actions">
                    <button class="btn btn-outline" ?disabled=${bulkDisabled} @click=${() => this._bulkToggleSelected(true)}>
                      ${this._bulkActionInProgress ? "Working\u2026" : "Enable all"}
                    </button>
                    <button class="btn btn-outline" ?disabled=${bulkDisabled} @click=${() => this._bulkToggleSelected(false)}>
                      ${this._bulkActionInProgress ? "Working\u2026" : "Disable all"}
                    </button>
                    <button class="btn btn-outline btn-danger" ?disabled=${bulkDisabled} @click=${() => this._bulkSoftDeleteSelected()}>
                      ${this._bulkActionInProgress ? "Working\u2026" : "Soft-delete selected"}
                    </button>
                    <button class="btn btn-ghost" ?disabled=${this._bulkActionInProgress} @click=${() => this._clearAutomationSelection()}>
                      Clear
                    </button>
                  </div>
                </div>
              ` : ""}
              <div class="automations-grid">
              ${filteredAutomations.map((a4) => {
      const isDraft = !!a4._draft;
      const expanded = !!this._expandedAutomations[a4.entity_id];
      const isOn = this._automationIsEnabled(a4);
      const automationId = a4.automation_id || "";
      const hasAutomationId = !!automationId;
      const canToggle = hasAutomationId && !this._bulkActionInProgress;
      const versionCount = a4.version_count || null;
      const deleting = this._deletingAutomation[automationId];
      const loadingChat = this._loadingToChat[automationId];
      const burgerOpen = this._openBurgerMenu === automationId;
      return x`
                  <div class="card" style="padding:12px 14px;${isDraft ? "border-color:#f59e0b;box-shadow:0 0 0 1px #f59e0b;" : ""}">
                    <div class="card-header" style="margin-bottom:6px;">
                      ${this._bulkEditMode && hasAutomationId ? x`
                        <label class="card-select">
                          <input type="checkbox"
                            .checked=${!!this._selectedAutomationIds[automationId]}
                            ?disabled=${this._bulkActionInProgress}
                            @click=${(e5) => e5.stopPropagation()}
                            @change=${(e5) => this._toggleAutomationSelection(automationId, e5)}>
                        </label>
                      ` : ""}
                      <h3 style="flex:1;font-size:14px;margin:0;">${a4.alias}</h3>
                      ${hasAutomationId ? x`
                        <div class="burger-menu-wrapper" style="margin-left:6px;">
                          <button class="burger-btn" @click=${(e5) => this._toggleBurgerMenu(automationId, e5)}
                            ?disabled=${this._bulkActionInProgress}
                            title="More actions">
                            <ha-icon icon="mdi:dots-vertical" style="--mdc-icon-size:16px;"></ha-icon>
                          </button>
                          ${burgerOpen ? x`
                            <div class="burger-dropdown">
                              <button class="burger-item danger" ?disabled=${deleting}
                                @click=${(e5) => {
        e5.stopPropagation();
        this._openBurgerMenu = null;
        this._softDeleteAutomation(automationId);
      }}>
                                <ha-icon icon="mdi:trash-can-outline" style="--mdc-icon-size:14px;"></ha-icon>
                                ${deleting ? "Deleting\u2026" : "Delete"}
                              </button>
                            </div>
                          ` : ""}
                        </div>
                      ` : ""}
                    </div>

                    <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;flex-wrap:wrap;">
                      <label class="toggle-switch" title="${canToggle ? isOn ? "Enabled" : "Disabled" : "Unavailable \u2014 automation id not resolved"}"
                        style="${canToggle ? "" : "opacity:0.45;cursor:not-allowed;"}"
                        @click=${() => {
        if (!canToggle) {
          this._showToast("Unable to toggle: automation id was not resolved. Reload and try again.", "error");
        }
      }}>
                        <input type="checkbox"
                          .checked=${isOn}
                          ?disabled=${!canToggle}
                          @click=${(e5) => e5.stopPropagation()}
                          @change=${(e5) => {
        if (!canToggle)
          return;
        this._toggleAutomation(a4.entity_id, automationId, e5.target.checked);
      }}>
                        <div class="toggle-track ${isOn ? "on" : ""}">
                          <div class="toggle-thumb"></div>
                        </div>
                      </label>
                      ${isDraft ? x`
                      <button class="btn btn-primary" style="font-size:11px;padding:3px 8px;"
                        @click=${() => {
        this._activeSessionId = a4._linked_session;
        this._activeTab = "chat";
        this._openSession(a4._linked_session);
      }}>
                        <ha-icon icon="mdi:chat-processing-outline" style="--mdc-icon-size:13px;"></ha-icon>
                        Define in Chat
                      </button>
                      <button class="btn btn-outline" style="font-size:11px;padding:3px 8px;margin-left:auto;"
                        @click=${() => this._dismissDraft(a4._draft_id)}>
                        <ha-icon icon="mdi:close" style="--mdc-icon-size:13px;"></ha-icon>
                        Dismiss
                      </button>` : x`
                      <button class="btn btn-outline" style="font-size:11px;padding:3px 8px;" ?disabled=${!hasAutomationId || loadingChat || this._bulkActionInProgress}
                        @click=${() => this._loadAutomationToChat(automationId)}>
                        <ha-icon icon="mdi:chat-processing-outline" style="--mdc-icon-size:13px;"></ha-icon>
                        ${loadingChat ? "Loading\u2026" : "Refine in chat"}
                      </button>`}
                    </div>

                    <div class="card-tabs">
                      <span class="label">View:</span>
                      ${a4.trigger?.length || a4.action?.length ? x`
                        <button class="card-tab ${this._cardActiveTab[a4.entity_id] === "flow" ? "active" : ""}"
                          @click=${() => {
        this._cardActiveTab = { ...this._cardActiveTab, [a4.entity_id]: this._cardActiveTab[a4.entity_id] === "flow" ? null : "flow" };
      }}>
                          <ha-icon icon="mdi:sitemap-outline" style="--mdc-icon-size:14px;"></ha-icon> Flow
                        </button>
                        <span class="card-tab-sep">|</span>
                      ` : ""}
                      ${a4.yaml_text ? x`
                        <button class="card-tab ${this._cardActiveTab[a4.entity_id] === "yaml" ? "active" : ""}"
                          @click=${() => {
        this._cardActiveTab = { ...this._cardActiveTab, [a4.entity_id]: this._cardActiveTab[a4.entity_id] === "yaml" ? null : "yaml" };
      }}>
                          <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:14px;"></ha-icon> YAML
                        </button>
                        <span class="card-tab-sep">|</span>
                      ` : ""}
                      ${hasAutomationId ? x`
                        <button class="card-tab ${this._cardActiveTab[a4.entity_id] === "history" ? "active" : ""}"
                          @click=${() => {
        const isActive = this._cardActiveTab[a4.entity_id] === "history";
        this._cardActiveTab = { ...this._cardActiveTab, [a4.entity_id]: isActive ? null : "history" };
        if (!isActive && !this._versions[automationId]) {
          this._versionHistoryOpen = { ...this._versionHistoryOpen, [automationId]: true };
          this._loadVersionHistory(automationId);
        }
      }}>
                          History
                        </button>
                      ` : ""}
                      <ha-icon icon="mdi:chevron-down"
                        class="card-chevron ${this._cardActiveTab[a4.entity_id] ? "open" : ""}"
                        style="margin-left:auto;"
                        title="Expand details"
                        @click=${() => {
        const current = this._cardActiveTab[a4.entity_id];
        if (current) {
          this._cardActiveTab = { ...this._cardActiveTab, [a4.entity_id]: null };
        } else {
          this._cardActiveTab = { ...this._cardActiveTab, [a4.entity_id]: "flow" };
        }
      }}></ha-icon>
                    </div>

                    ${this._cardActiveTab[a4.entity_id] === "flow" && (a4.trigger?.length || a4.action?.length) ? this._renderAutomationFlowchart(a4) : ""}

                    ${this._cardActiveTab[a4.entity_id] === "yaml" && a4.yaml_text ? this._renderYamlEditor(
        `yaml_${a4.entity_id}`,
        a4.yaml_text,
        (key) => this._saveActiveAutomationYaml(a4.automation_id, key)
      ) : ""}

                    ${this._cardActiveTab[a4.entity_id] === "history" && hasAutomationId ? this._renderVersionHistoryDrawer(a4) : ""}
                  </div>
                `;
    })}
              </div>
              ${filteredAutomations.length === 0 && this._automations.length > 0 ? x`<div style="text-align:center;opacity:0.45;padding:24px 0;">No automations match "${this._automationFilter}"</div>` : ""}
              ${this._renderDeletedSection()}
            ` : x`<div style="text-align:center;padding:32px 0;">
              <ha-icon icon="mdi:robot-vacuum-variant" style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"></ha-icon>
              <p style="opacity:0.45;margin:0 0 12px;">No automations yet.</p>
              <button class="btn btn-primary" @click=${() => {
      this._newAutoName = "";
      this._showNewAutoDialog = true;
    }}>
                <ha-icon icon="mdi:plus" style="--mdc-icon-size:14px;"></ha-icon>
                New Automation
              </button>
            </div>`}
        ` : ""}
        ${this._automationsSubTab === "suggestions" ? x`
          ${this._renderProactiveSuggestions()}
          <h2 style="margin-top:0;">AI Recommendations</h2>
        ${this._suggestions.length === 0 ? x`
              <div style="display:flex; flex-direction:column; align-items:center; padding:32px 0; gap:12px;">
                <ha-icon icon="mdi:robot-vacuum-variant" style="--mdc-icon-size:56px;opacity:0.35;"></ha-icon>
                <p style="opacity:0.45;margin:0;">No recommendations yet — background analysis runs hourly.</p>
                <button class="btn btn-primary" style="margin-top:8px;" ?disabled=${this._generatingSuggestions}
                  @click=${() => this._triggerGenerateSuggestions()}>
                  <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size:16px;"></ha-icon>
                  ${this._generatingSuggestions ? "Analyzing\u2026" : "Generate Suggestions"}
                </button>
              </div>
            ` : this._suggestions.map((item) => {
      const auto = item.automation || item.automation_data;
      const risk = item.risk_assessment || auto?.risk_assessment || null;
      const key = `sug_${auto.alias}`;
      const expanded = !!this._expandedAutomations[key];
      const origYaml = item.automation_yaml || "";
      return x`
                <div class="card">
                  <div class="card-header">
                    <h3>${auto.alias}</h3>
                    <div style="display:flex; flex-wrap:wrap; gap:6px; justify-content:flex-end;">
                      <div class="chip suggestion">RECOMMENDED</div>
                      ${(risk?.scrutiny_tags || []).map(
        (tag) => x`<div class="chip" style="background:rgba(33,150,243,0.10); color:var(--primary-color); border:1px solid rgba(33,150,243,0.18);">${tag}</div>`
      )}
                    </div>
                  </div>
                  ${auto.description ? x`<p>${auto.description}</p>` : ""}
                  ${risk?.level === "elevated" ? x`
                        <div class="proposal-status" style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25); margin-bottom:12px;">
                          <ha-icon icon="mdi:alert-outline"></ha-icon>
                          <div>
                            <strong>Elevated risk review recommended.</strong>
                            <div style="margin-top:4px;">${risk.summary}</div>
                            ${risk.reasons?.length ? x`<div style="margin-top:6px; font-size:12px;">${risk.reasons.join(" ")}</div>` : ""}
                          </div>
                        </div>
                      ` : ""}

                  ${this._renderAutomationFlowchart(auto)}

                  <div class="expand-toggle" @click=${() => this._toggleExpandAutomation(key)}>
                    <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:13px;"></ha-icon>
                    ${expanded ? "Hide YAML" : "Edit YAML"}
                  </div>
                  ${expanded ? this._renderYamlEditor(key, origYaml) : ""}

                  <div class="card-actions">
                    <button class="btn btn-outline" @click=${() => this._discardSuggestion(item)}>
                      <ha-icon icon="mdi:trash-can-outline" style="--mdc-icon-size:13px;"></ha-icon>
                      Discard
                    </button>
                    <button class="btn btn-primary" ?disabled=${!!this._savingYaml[key]}
                      @click=${() => this._createSuggestionWithEdits(auto, key, origYaml)}>
                      <ha-icon icon="mdi:plus" style="--mdc-icon-size:13px;"></ha-icon>
                      ${this._savingYaml[key] ? "Creating\u2026" : "Create Automation"}
                    </button>
                  </div>
                </div>
              `;
    })}
        ` : ""}
      </div>
      ${this._renderDiffViewer()}
      ${this._renderNewAutomationDialog()}
    `;
  }
  // -------------------------------------------------------------------------
  // Proactive suggestions section (automations tab)
  // -------------------------------------------------------------------------
  _renderProactiveSuggestions() {
    if (!this._showProactive && !this._proactiveSuggestions.length)
      return "";
    return x`
      <div style="margin-bottom:16px;">
        <div class="filter-row" style="margin-bottom:8px;">
          <h2 style="margin:0;display:flex;align-items:center;gap:8px;">
            <ha-icon icon="mdi:lightbulb-auto-outline" style="--mdc-icon-size:20px;color:#f59e0b;"></ha-icon>
            Suggested
            ${this._proactiveSuggestions.length > 0 ? x`<span style="background:#f59e0b;color:#000;border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600;">${this._proactiveSuggestions.length}</span>` : ""}
          </h2>
          <div style="display:flex;align-items:center;gap:8px;">
            <button class="btn" style="font-size:11px;" ?disabled=${this._loadingProactive}
              @click=${() => this._triggerPatternScan()}>
              <ha-icon icon="mdi:refresh" style="--mdc-icon-size:13px;"></ha-icon>
              ${this._loadingProactive ? "Scanning\u2026" : "Scan Now"}
            </button>
            <button class="btn" style="font-size:11px;"
              @click=${() => {
      this._showProactive = !this._showProactive;
    }}>
              <ha-icon icon="mdi:chevron-${this._showProactive ? "up" : "down"}" style="--mdc-icon-size:13px;"></ha-icon>
              ${this._showProactive ? "Hide" : "Show"}
            </button>
          </div>
        </div>

        ${this._showProactive ? x`
          ${this._proactiveSuggestions.length === 0 ? x`<div class="card" style="padding:16px;text-align:center;opacity:0.6;font-size:13px;">
                <ha-icon icon="mdi:check-circle-outline" style="--mdc-icon-size:24px;margin-bottom:8px;display:block;"></ha-icon>
                No new suggestions. Patterns are analyzed every 15 minutes.
              </div>` : x`
              <div class="automations-grid">
                ${this._proactiveSuggestions.map((s6) => {
      const expanded = !!this._proactiveExpanded[s6.suggestion_id];
      const accepting = !!this._acceptingProactive[s6.suggestion_id];
      const dismissing = !!this._dismissingProactive[s6.suggestion_id];
      const yamlKey = `proactive_${s6.suggestion_id}`;
      const editedYaml = this._editedYaml[yamlKey];
      const displayYaml = editedYaml !== void 0 ? editedYaml : s6.automation_yaml;
      const confidencePct = Math.round((s6.confidence || 0) * 100);
      const confidenceColor = confidencePct >= 75 ? "#22c55e" : confidencePct >= 50 ? "#f59e0b" : "#ef4444";
      return x`
                    <div class="card" style="padding:12px 14px;border-color:#f59e0b;border-left:3px solid #f59e0b;">
                      <div class="card-header" style="margin-bottom:6px;">
                        <h3 style="flex:1;font-size:14px;margin:0;">${s6.description}</h3>
                        <span style="font-size:11px;color:${confidenceColor};font-weight:600;margin-left:8px;">${confidencePct}%</span>
                        <div class="chip ai-managed" style="margin-left:6px;">PATTERN</div>
                      </div>

                      <div style="font-size:11px;opacity:0.6;margin-bottom:8px;">
                        ${s6.evidence_summary || ""}
                        ${s6.source === "hybrid" ? x` <span style="opacity:0.5;">· AI-enhanced</span>` : ""}
                      </div>

                      <span class="expand-toggle" style="padding:0;margin:0 0 8px;" @click=${() => {
        this._proactiveExpanded = { ...this._proactiveExpanded, [s6.suggestion_id]: !expanded };
      }}>
                        <ha-icon icon="mdi:chevron-${expanded ? "up" : "down"}" style="--mdc-icon-size:13px;"></ha-icon>
                        ${expanded ? "Hide YAML" : "Show YAML"}
                      </span>

                      ${expanded ? x`
                        <div style="margin-bottom:8px;">
                          <textarea class="yaml-editor"
                            style="width:100%;min-height:120px;font-family:monospace;font-size:12px;background:var(--primary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px;resize:vertical;"
                            .value=${displayYaml}
                            @input=${(e5) => {
        this._editedYaml = { ...this._editedYaml, [yamlKey]: e5.target.value };
      }}>
                          </textarea>
                        </div>
                      ` : ""}

                      <div style="display:flex;gap:8px;justify-content:flex-end;">
                        <button class="btn" style="font-size:11px;" ?disabled=${dismissing}
                          @click=${() => this._snoozeProactiveSuggestion(s6.suggestion_id)}>
                          <ha-icon icon="mdi:clock-outline" style="--mdc-icon-size:13px;"></ha-icon>
                          Snooze
                        </button>
                        <button class="btn" style="font-size:11px;" ?disabled=${dismissing}
                          @click=${() => this._dismissProactiveSuggestion(s6.suggestion_id)}>
                          <ha-icon icon="mdi:close" style="--mdc-icon-size:13px;"></ha-icon>
                          ${dismissing ? "Dismissing\u2026" : "Dismiss"}
                        </button>
                        <button class="btn btn-primary" style="font-size:11px;" ?disabled=${accepting}
                          @click=${() => this._acceptProactiveSuggestion(s6.suggestion_id, editedYaml)}>
                          <ha-icon icon="mdi:check" style="--mdc-icon-size:13px;"></ha-icon>
                          ${accepting ? "Creating\u2026" : "Accept"}
                        </button>
                      </div>
                    </div>
                  `;
    })}
              </div>
            `}
        ` : ""}
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Settings tab
  // -------------------------------------------------------------------------
  _renderSettings() {
    if (!this._config) {
      return x`
        <div class="scroll-view" style="display:flex; justify-content:center; padding-top:64px;">
          <ha-circular-progress active></ha-circular-progress>
        </div>
      `;
    }
    const isAnthropic = this._config.llm_provider === "anthropic";
    const isOpenAI = this._config.llm_provider === "openai";
    return x`
      <div class="scroll-view">
        <div class="settings-form">
          <h2>Integration Settings</h2>

          <div class="form-group">
            <label>LLM Provider</label>
            <select
              .value=${this._config.llm_provider}
              @change=${(e5) => this._updateConfig("llm_provider", e5.target.value)}
              style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama (Local)</option>
            </select>
          </div>

          ${isAnthropic ? x`
                <div class="form-group">
                  <label>Anthropic API Key</label>
                  ${this._config.anthropic_api_key_set ? x`<div class="key-hint">Current key: ${this._config.anthropic_api_key_hint}</div>` : x`<div class="key-not-set">No API key set.</div>`}
                  <ha-textfield
                    label="${this._config.anthropic_api_key_set ? "Enter new key to replace" : "Enter API key"}"
                    type="password"
                    .value=${this._newApiKey}
                    @input=${(e5) => this._newApiKey = e5.target.value}
                    placeholder="sk-ant-..."
                    style="margin-top:8px;"
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Anthropic Model"
                    .value=${this._config.anthropic_model}
                    @input=${(e5) => this._updateConfig("anthropic_model", e5.target.value)}
                  ></ha-textfield>
                </div>
              ` : isOpenAI ? x`
                <div class="form-group">
                  <label>OpenAI API Key</label>
                  ${this._config.openai_api_key_set ? x`<div class="key-hint">Current key: ${this._config.openai_api_key_hint}</div>` : x`<div class="key-not-set">No API key set.</div>`}
                  <ha-textfield
                    label="${this._config.openai_api_key_set ? "Enter new key to replace" : "Enter API key"}"
                    type="password"
                    .value=${this._newApiKey}
                    @input=${(e5) => this._newApiKey = e5.target.value}
                    placeholder="sk-..."
                    style="margin-top:8px;"
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="OpenAI Model"
                    .value=${this._config.openai_model}
                    @input=${(e5) => this._updateConfig("openai_model", e5.target.value)}
                  ></ha-textfield>
                </div>
            ` : x`
                <div class="form-group">
                  <ha-textfield
                    label="Ollama Host"
                    .value=${this._config.ollama_host}
                    @input=${(e5) => this._updateConfig("ollama_host", e5.target.value)}
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Ollama Model"
                    .value=${this._config.ollama_model}
                    @input=${(e5) => this._updateConfig("ollama_model", e5.target.value)}
                  ></ha-textfield>
                </div>
              `}

          <h3 style="border-bottom:1px solid var(--divider-color); padding-bottom:8px;">Background Services</h3>

          <div style="margin-top:16px;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;">
              <ha-switch
                .checked=${this._config.collector_enabled}
                @change=${(e5) => this._updateConfig("collector_enabled", e5.target.checked)}
              ></ha-switch>
              <label>Data Collector (AI Analysis)</label>
            </div>

            ${this._config.collector_enabled ? x`
                  <div style="padding-left:20px; border-left:2px solid var(--divider-color); margin-bottom:20px;">
                    <div class="form-group">
                      <label>Mode</label>
                      <select
                        .value=${this._config.collector_mode}
                        @change=${(e5) => this._updateConfig("collector_mode", e5.target.value)}
                        style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
                      >
                        <option value="continuous">Continuous</option>
                        <option value="scheduled">Scheduled Window</option>
                      </select>
                    </div>
                    <div class="form-group">
                      <ha-textfield label="Interval (seconds)" type="number"
                        .value=${this._config.collector_interval}
                        @input=${(e5) => this._updateConfig("collector_interval", parseInt(e5.target.value))}
                      ></ha-textfield>
                    </div>
                    ${this._config.collector_mode === "scheduled" ? x`
                          <div style="display:flex; gap:12px;">
                            <ha-textfield label="Start (HH:MM)" .value=${this._config.collector_start_time} @input=${(e5) => this._updateConfig("collector_start_time", e5.target.value)} style="flex:1;"></ha-textfield>
                            <ha-textfield label="End (HH:MM)"   .value=${this._config.collector_end_time}   @input=${(e5) => this._updateConfig("collector_end_time", e5.target.value)} style="flex:1;"></ha-textfield>
                          </div>
                        ` : ""}
                  </div>
                ` : ""}

            <div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;">
              <ha-switch
                .checked=${this._config.discovery_enabled}
                @change=${(e5) => this._updateConfig("discovery_enabled", e5.target.checked)}
              ></ha-switch>
              <label>Network Discovery</label>
            </div>

            ${this._config.discovery_enabled ? x`
                  <div style="padding-left:20px; border-left:2px solid var(--divider-color); margin-bottom:20px;">
                    <div class="form-group">
                      <label>Mode</label>
                      <select
                        .value=${this._config.discovery_mode}
                        @change=${(e5) => this._updateConfig("discovery_mode", e5.target.value)}
                        style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
                      >
                        <option value="continuous">Continuous</option>
                        <option value="scheduled">Scheduled Window</option>
                      </select>
                    </div>
                    <div class="form-group">
                      <ha-textfield label="Interval (seconds)" type="number"
                        .value=${this._config.discovery_interval}
                        @input=${(e5) => this._updateConfig("discovery_interval", parseInt(e5.target.value))}
                      ></ha-textfield>
                    </div>
                    ${this._config.discovery_mode === "scheduled" ? x`
                          <div style="display:flex; gap:12px;">
                            <ha-textfield label="Start (HH:MM)" .value=${this._config.discovery_start_time} @input=${(e5) => this._updateConfig("discovery_start_time", e5.target.value)} style="flex:1;"></ha-textfield>
                            <ha-textfield label="End (HH:MM)"   .value=${this._config.discovery_end_time}   @input=${(e5) => this._updateConfig("discovery_end_time", e5.target.value)} style="flex:1;"></ha-textfield>
                          </div>
                        ` : ""}
                  </div>
                ` : ""}
          </div>

          <div class="save-bar">
            <mwc-button raised @click=${this._saveConfig} ?disabled=${this._savingConfig}>
              ${this._savingConfig ? "Saving\u2026" : "Save Settings"}
            </mwc-button>
          </div>
        </div>
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  _formatDate(iso) {
    if (!iso)
      return "";
    try {
      const d3 = new Date(iso);
      const now = /* @__PURE__ */ new Date();
      const diffMs = now - d3;
      if (diffMs < 6e4)
        return "just now";
      if (diffMs < 36e5)
        return `${Math.floor(diffMs / 6e4)}m ago`;
      if (diffMs < 864e5)
        return `${Math.floor(diffMs / 36e5)}h ago`;
      return d3.toLocaleDateString();
    } catch {
      return "";
    }
  }
  _formatTime(iso) {
    if (!iso)
      return "";
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }
};
customElements.define("selora-ai-architect", SeloraAIArchitectPanel);
/*! Bundled license information:

@lit/reactive-element/css-tag.js:
  (**
   * @license
   * Copyright 2019 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

@lit/reactive-element/reactive-element.js:
  (**
   * @license
   * Copyright 2017 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/lit-html.js:
  (**
   * @license
   * Copyright 2017 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-element/lit-element.js:
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

lit-html/directive.js:
  (**
   * @license
   * Copyright 2017 Google LLC
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
