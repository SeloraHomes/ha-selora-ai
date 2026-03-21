// node_modules/@lit/reactive-element/css-tag.js
var t = window;
var e = t.ShadowRoot && (void 0 === t.ShadyCSS || t.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype;
var s = Symbol();
var n = /* @__PURE__ */ new WeakMap();
var o = class {
  constructor(t3, e4, n5) {
    if (this._$cssResult$ = true, n5 !== s)
      throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
    this.cssText = t3, this.t = e4;
  }
  get styleSheet() {
    let t3 = this.o;
    const s5 = this.t;
    if (e && void 0 === t3) {
      const e4 = void 0 !== s5 && 1 === s5.length;
      e4 && (t3 = n.get(s5)), void 0 === t3 && ((this.o = t3 = new CSSStyleSheet()).replaceSync(this.cssText), e4 && n.set(s5, t3));
    }
    return t3;
  }
  toString() {
    return this.cssText;
  }
};
var r = (t3) => new o("string" == typeof t3 ? t3 : t3 + "", void 0, s);
var i = (t3, ...e4) => {
  const n5 = 1 === t3.length ? t3[0] : e4.reduce((e5, s5, n6) => e5 + ((t4) => {
    if (true === t4._$cssResult$)
      return t4.cssText;
    if ("number" == typeof t4)
      return t4;
    throw Error("Value passed to 'css' function must be a 'css' function result: " + t4 + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
  })(s5) + t3[n6 + 1], t3[0]);
  return new o(n5, t3, s);
};
var S = (s5, n5) => {
  e ? s5.adoptedStyleSheets = n5.map((t3) => t3 instanceof CSSStyleSheet ? t3 : t3.styleSheet) : n5.forEach((e4) => {
    const n6 = document.createElement("style"), o5 = t.litNonce;
    void 0 !== o5 && n6.setAttribute("nonce", o5), n6.textContent = e4.cssText, s5.appendChild(n6);
  });
};
var c = e ? (t3) => t3 : (t3) => t3 instanceof CSSStyleSheet ? ((t4) => {
  let e4 = "";
  for (const s5 of t4.cssRules)
    e4 += s5.cssText;
  return r(e4);
})(t3) : t3;

// node_modules/@lit/reactive-element/reactive-element.js
var s2;
var e2 = window;
var r2 = e2.trustedTypes;
var h = r2 ? r2.emptyScript : "";
var o2 = e2.reactiveElementPolyfillSupport;
var n2 = { toAttribute(t3, i3) {
  switch (i3) {
    case Boolean:
      t3 = t3 ? h : null;
      break;
    case Object:
    case Array:
      t3 = null == t3 ? t3 : JSON.stringify(t3);
  }
  return t3;
}, fromAttribute(t3, i3) {
  let s5 = t3;
  switch (i3) {
    case Boolean:
      s5 = null !== t3;
      break;
    case Number:
      s5 = null === t3 ? null : Number(t3);
      break;
    case Object:
    case Array:
      try {
        s5 = JSON.parse(t3);
      } catch (t4) {
        s5 = null;
      }
  }
  return s5;
} };
var a = (t3, i3) => i3 !== t3 && (i3 == i3 || t3 == t3);
var l = { attribute: true, type: String, converter: n2, reflect: false, hasChanged: a };
var d = "finalized";
var u = class extends HTMLElement {
  constructor() {
    super(), this._$Ei = /* @__PURE__ */ new Map(), this.isUpdatePending = false, this.hasUpdated = false, this._$El = null, this._$Eu();
  }
  static addInitializer(t3) {
    var i3;
    this.finalize(), (null !== (i3 = this.h) && void 0 !== i3 ? i3 : this.h = []).push(t3);
  }
  static get observedAttributes() {
    this.finalize();
    const t3 = [];
    return this.elementProperties.forEach((i3, s5) => {
      const e4 = this._$Ep(s5, i3);
      void 0 !== e4 && (this._$Ev.set(e4, s5), t3.push(e4));
    }), t3;
  }
  static createProperty(t3, i3 = l) {
    if (i3.state && (i3.attribute = false), this.finalize(), this.elementProperties.set(t3, i3), !i3.noAccessor && !this.prototype.hasOwnProperty(t3)) {
      const s5 = "symbol" == typeof t3 ? Symbol() : "__" + t3, e4 = this.getPropertyDescriptor(t3, s5, i3);
      void 0 !== e4 && Object.defineProperty(this.prototype, t3, e4);
    }
  }
  static getPropertyDescriptor(t3, i3, s5) {
    return { get() {
      return this[i3];
    }, set(e4) {
      const r4 = this[t3];
      this[i3] = e4, this.requestUpdate(t3, r4, s5);
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
      const t4 = this.properties, i3 = [...Object.getOwnPropertyNames(t4), ...Object.getOwnPropertySymbols(t4)];
      for (const s5 of i3)
        this.createProperty(s5, t4[s5]);
    }
    return this.elementStyles = this.finalizeStyles(this.styles), true;
  }
  static finalizeStyles(i3) {
    const s5 = [];
    if (Array.isArray(i3)) {
      const e4 = new Set(i3.flat(1 / 0).reverse());
      for (const i4 of e4)
        s5.unshift(c(i4));
    } else
      void 0 !== i3 && s5.push(c(i3));
    return s5;
  }
  static _$Ep(t3, i3) {
    const s5 = i3.attribute;
    return false === s5 ? void 0 : "string" == typeof s5 ? s5 : "string" == typeof t3 ? t3.toLowerCase() : void 0;
  }
  _$Eu() {
    var t3;
    this._$E_ = new Promise((t4) => this.enableUpdating = t4), this._$AL = /* @__PURE__ */ new Map(), this._$Eg(), this.requestUpdate(), null === (t3 = this.constructor.h) || void 0 === t3 || t3.forEach((t4) => t4(this));
  }
  addController(t3) {
    var i3, s5;
    (null !== (i3 = this._$ES) && void 0 !== i3 ? i3 : this._$ES = []).push(t3), void 0 !== this.renderRoot && this.isConnected && (null === (s5 = t3.hostConnected) || void 0 === s5 || s5.call(t3));
  }
  removeController(t3) {
    var i3;
    null === (i3 = this._$ES) || void 0 === i3 || i3.splice(this._$ES.indexOf(t3) >>> 0, 1);
  }
  _$Eg() {
    this.constructor.elementProperties.forEach((t3, i3) => {
      this.hasOwnProperty(i3) && (this._$Ei.set(i3, this[i3]), delete this[i3]);
    });
  }
  createRenderRoot() {
    var t3;
    const s5 = null !== (t3 = this.shadowRoot) && void 0 !== t3 ? t3 : this.attachShadow(this.constructor.shadowRootOptions);
    return S(s5, this.constructor.elementStyles), s5;
  }
  connectedCallback() {
    var t3;
    void 0 === this.renderRoot && (this.renderRoot = this.createRenderRoot()), this.enableUpdating(true), null === (t3 = this._$ES) || void 0 === t3 || t3.forEach((t4) => {
      var i3;
      return null === (i3 = t4.hostConnected) || void 0 === i3 ? void 0 : i3.call(t4);
    });
  }
  enableUpdating(t3) {
  }
  disconnectedCallback() {
    var t3;
    null === (t3 = this._$ES) || void 0 === t3 || t3.forEach((t4) => {
      var i3;
      return null === (i3 = t4.hostDisconnected) || void 0 === i3 ? void 0 : i3.call(t4);
    });
  }
  attributeChangedCallback(t3, i3, s5) {
    this._$AK(t3, s5);
  }
  _$EO(t3, i3, s5 = l) {
    var e4;
    const r4 = this.constructor._$Ep(t3, s5);
    if (void 0 !== r4 && true === s5.reflect) {
      const h3 = (void 0 !== (null === (e4 = s5.converter) || void 0 === e4 ? void 0 : e4.toAttribute) ? s5.converter : n2).toAttribute(i3, s5.type);
      this._$El = t3, null == h3 ? this.removeAttribute(r4) : this.setAttribute(r4, h3), this._$El = null;
    }
  }
  _$AK(t3, i3) {
    var s5;
    const e4 = this.constructor, r4 = e4._$Ev.get(t3);
    if (void 0 !== r4 && this._$El !== r4) {
      const t4 = e4.getPropertyOptions(r4), h3 = "function" == typeof t4.converter ? { fromAttribute: t4.converter } : void 0 !== (null === (s5 = t4.converter) || void 0 === s5 ? void 0 : s5.fromAttribute) ? t4.converter : n2;
      this._$El = r4, this[r4] = h3.fromAttribute(i3, t4.type), this._$El = null;
    }
  }
  requestUpdate(t3, i3, s5) {
    let e4 = true;
    void 0 !== t3 && (((s5 = s5 || this.constructor.getPropertyOptions(t3)).hasChanged || a)(this[t3], i3) ? (this._$AL.has(t3) || this._$AL.set(t3, i3), true === s5.reflect && this._$El !== t3 && (void 0 === this._$EC && (this._$EC = /* @__PURE__ */ new Map()), this._$EC.set(t3, s5))) : e4 = false), !this.isUpdatePending && e4 && (this._$E_ = this._$Ej());
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
    this.hasUpdated, this._$Ei && (this._$Ei.forEach((t4, i4) => this[i4] = t4), this._$Ei = void 0);
    let i3 = false;
    const s5 = this._$AL;
    try {
      i3 = this.shouldUpdate(s5), i3 ? (this.willUpdate(s5), null === (t3 = this._$ES) || void 0 === t3 || t3.forEach((t4) => {
        var i4;
        return null === (i4 = t4.hostUpdate) || void 0 === i4 ? void 0 : i4.call(t4);
      }), this.update(s5)) : this._$Ek();
    } catch (t4) {
      throw i3 = false, this._$Ek(), t4;
    }
    i3 && this._$AE(s5);
  }
  willUpdate(t3) {
  }
  _$AE(t3) {
    var i3;
    null === (i3 = this._$ES) || void 0 === i3 || i3.forEach((t4) => {
      var i4;
      return null === (i4 = t4.hostUpdated) || void 0 === i4 ? void 0 : i4.call(t4);
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
    void 0 !== this._$EC && (this._$EC.forEach((t4, i3) => this._$EO(i3, this[i3], t4)), this._$EC = void 0), this._$Ek();
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
var w = (t3) => (i3, ...s5) => ({ _$litType$: t3, strings: i3, values: s5 });
var x = w(1);
var b = w(2);
var T = Symbol.for("lit-noChange");
var A = Symbol.for("lit-nothing");
var E = /* @__PURE__ */ new WeakMap();
var C = r3.createTreeWalker(r3, 129, null, false);
function P(t3, i3) {
  if (!Array.isArray(t3) || !t3.hasOwnProperty("raw"))
    throw Error("invalid template strings array");
  return void 0 !== e3 ? e3.createHTML(i3) : i3;
}
var V = (t3, i3) => {
  const s5 = t3.length - 1, e4 = [];
  let l4, r4 = 2 === i3 ? "<svg>" : "", u3 = f;
  for (let i4 = 0; i4 < s5; i4++) {
    const s6 = t3[i4];
    let d3, c3, v2 = -1, a3 = 0;
    for (; a3 < s6.length && (u3.lastIndex = a3, c3 = u3.exec(s6), null !== c3); )
      a3 = u3.lastIndex, u3 === f ? "!--" === c3[1] ? u3 = _ : void 0 !== c3[1] ? u3 = m : void 0 !== c3[2] ? (y.test(c3[2]) && (l4 = RegExp("</" + c3[2], "g")), u3 = p) : void 0 !== c3[3] && (u3 = p) : u3 === p ? ">" === c3[0] ? (u3 = null != l4 ? l4 : f, v2 = -1) : void 0 === c3[1] ? v2 = -2 : (v2 = u3.lastIndex - c3[2].length, d3 = c3[1], u3 = void 0 === c3[3] ? p : '"' === c3[3] ? $ : g) : u3 === $ || u3 === g ? u3 = p : u3 === _ || u3 === m ? u3 = f : (u3 = p, l4 = void 0); // nosemgrep
    const w2 = u3 === p && t3[i4 + 1].startsWith("/>") ? " " : "";
    r4 += u3 === f ? s6 + h2 : v2 >= 0 ? (e4.push(d3), s6.slice(0, v2) + o3 + s6.slice(v2) + n3 + w2) : s6 + n3 + (-2 === v2 ? (e4.push(void 0), i4) : w2);
  }
  return [P(t3, r4 + (t3[s5] || "<?>") + (2 === i3 ? "</svg>" : "")), e4];
};
var N = class _N {
  constructor({ strings: t3, _$litType$: i3 }, e4) {
    let h3;
    this.parts = [];
    let r4 = 0, d3 = 0;
    const c3 = t3.length - 1, v2 = this.parts, [a3, f2] = V(t3, i3);
    if (this.el = _N.createElement(a3, e4), C.currentNode = this.el.content, 2 === i3) {
      const t4 = this.el.content, i4 = t4.firstChild;
      i4.remove(), t4.append(...i4.childNodes);
    }
    for (; null !== (h3 = C.nextNode()) && v2.length < c3; ) {
      if (1 === h3.nodeType) {
        if (h3.hasAttributes()) {
          const t4 = [];
          for (const i4 of h3.getAttributeNames())
            if (i4.endsWith(o3) || i4.startsWith(n3)) {
              const s5 = f2[d3++];
              if (t4.push(i4), void 0 !== s5) {
                const t5 = h3.getAttribute(s5.toLowerCase() + o3).split(n3), i5 = /([.?@])?(.*)/.exec(s5);
                v2.push({ type: 1, index: r4, name: i5[2], strings: t5, ctor: "." === i5[1] ? H : "?" === i5[1] ? L : "@" === i5[1] ? z : k });
              } else
                v2.push({ type: 6, index: r4 });
            }
          for (const i4 of t4)
            h3.removeAttribute(i4);
        }
        if (y.test(h3.tagName)) {
          const t4 = h3.textContent.split(n3), i4 = t4.length - 1;
          if (i4 > 0) {
            h3.textContent = s3 ? s3.emptyScript : "";
            for (let s5 = 0; s5 < i4; s5++)
              h3.append(t4[s5], u2()), C.nextNode(), v2.push({ type: 2, index: ++r4 });
            h3.append(t4[i4], u2());
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
  static createElement(t3, i3) {
    const s5 = r3.createElement("template");
    return s5.innerHTML = t3, s5;
  }
};
function S2(t3, i3, s5 = t3, e4) {
  var o5, n5, l4, h3;
  if (i3 === T)
    return i3;
  let r4 = void 0 !== e4 ? null === (o5 = s5._$Co) || void 0 === o5 ? void 0 : o5[e4] : s5._$Cl;
  const u3 = d2(i3) ? void 0 : i3._$litDirective$;
  return (null == r4 ? void 0 : r4.constructor) !== u3 && (null === (n5 = null == r4 ? void 0 : r4._$AO) || void 0 === n5 || n5.call(r4, false), void 0 === u3 ? r4 = void 0 : (r4 = new u3(t3), r4._$AT(t3, s5, e4)), void 0 !== e4 ? (null !== (l4 = (h3 = s5)._$Co) && void 0 !== l4 ? l4 : h3._$Co = [])[e4] = r4 : s5._$Cl = r4), void 0 !== r4 && (i3 = S2(t3, r4._$AS(t3, i3.values), r4, e4)), i3;
}
var M = class {
  constructor(t3, i3) {
    this._$AV = [], this._$AN = void 0, this._$AD = t3, this._$AM = i3;
  }
  get parentNode() {
    return this._$AM.parentNode;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  u(t3) {
    var i3;
    const { el: { content: s5 }, parts: e4 } = this._$AD, o5 = (null !== (i3 = null == t3 ? void 0 : t3.creationScope) && void 0 !== i3 ? i3 : r3).importNode(s5, true);
    C.currentNode = o5;
    let n5 = C.nextNode(), l4 = 0, h3 = 0, u3 = e4[0];
    for (; void 0 !== u3; ) {
      if (l4 === u3.index) {
        let i4;
        2 === u3.type ? i4 = new R(n5, n5.nextSibling, this, t3) : 1 === u3.type ? i4 = new u3.ctor(n5, u3.name, u3.strings, this, t3) : 6 === u3.type && (i4 = new Z(n5, this, t3)), this._$AV.push(i4), u3 = e4[++h3];
      }
      l4 !== (null == u3 ? void 0 : u3.index) && (n5 = C.nextNode(), l4++);
    }
    return C.currentNode = r3, o5;
  }
  v(t3) {
    let i3 = 0;
    for (const s5 of this._$AV)
      void 0 !== s5 && (void 0 !== s5.strings ? (s5._$AI(t3, s5, i3), i3 += s5.strings.length - 2) : s5._$AI(t3[i3])), i3++;
  }
};
var R = class _R {
  constructor(t3, i3, s5, e4) {
    var o5;
    this.type = 2, this._$AH = A, this._$AN = void 0, this._$AA = t3, this._$AB = i3, this._$AM = s5, this.options = e4, this._$Cp = null === (o5 = null == e4 ? void 0 : e4.isConnected) || void 0 === o5 || o5;
  }
  get _$AU() {
    var t3, i3;
    return null !== (i3 = null === (t3 = this._$AM) || void 0 === t3 ? void 0 : t3._$AU) && void 0 !== i3 ? i3 : this._$Cp;
  }
  get parentNode() {
    let t3 = this._$AA.parentNode;
    const i3 = this._$AM;
    return void 0 !== i3 && 11 === (null == t3 ? void 0 : t3.nodeType) && (t3 = i3.parentNode), t3;
  }
  get startNode() {
    return this._$AA;
  }
  get endNode() {
    return this._$AB;
  }
  _$AI(t3, i3 = this) {
    t3 = S2(this, t3, i3), d2(t3) ? t3 === A || null == t3 || "" === t3 ? (this._$AH !== A && this._$AR(), this._$AH = A) : t3 !== this._$AH && t3 !== T && this._(t3) : void 0 !== t3._$litType$ ? this.g(t3) : void 0 !== t3.nodeType ? this.$(t3) : v(t3) ? this.T(t3) : this._(t3);
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
    var i3;
    const { values: s5, _$litType$: e4 } = t3, o5 = "number" == typeof e4 ? this._$AC(t3) : (void 0 === e4.el && (e4.el = N.createElement(P(e4.h, e4.h[0]), this.options)), e4);
    if ((null === (i3 = this._$AH) || void 0 === i3 ? void 0 : i3._$AD) === o5)
      this._$AH.v(s5);
    else {
      const t4 = new M(o5, this), i4 = t4.u(this.options);
      t4.v(s5), this.$(i4), this._$AH = t4;
    }
  }
  _$AC(t3) {
    let i3 = E.get(t3.strings);
    return void 0 === i3 && E.set(t3.strings, i3 = new N(t3)), i3;
  }
  T(t3) {
    c2(this._$AH) || (this._$AH = [], this._$AR());
    const i3 = this._$AH;
    let s5, e4 = 0;
    for (const o5 of t3)
      e4 === i3.length ? i3.push(s5 = new _R(this.k(u2()), this.k(u2()), this, this.options)) : s5 = i3[e4], s5._$AI(o5), e4++;
    e4 < i3.length && (this._$AR(s5 && s5._$AB.nextSibling, e4), i3.length = e4);
  }
  _$AR(t3 = this._$AA.nextSibling, i3) {
    var s5;
    for (null === (s5 = this._$AP) || void 0 === s5 || s5.call(this, false, true, i3); t3 && t3 !== this._$AB; ) {
      const i4 = t3.nextSibling;
      t3.remove(), t3 = i4;
    }
  }
  setConnected(t3) {
    var i3;
    void 0 === this._$AM && (this._$Cp = t3, null === (i3 = this._$AP) || void 0 === i3 || i3.call(this, t3));
  }
};
var k = class {
  constructor(t3, i3, s5, e4, o5) {
    this.type = 1, this._$AH = A, this._$AN = void 0, this.element = t3, this.name = i3, this._$AM = e4, this.options = o5, s5.length > 2 || "" !== s5[0] || "" !== s5[1] ? (this._$AH = Array(s5.length - 1).fill(new String()), this.strings = s5) : this._$AH = A;
  }
  get tagName() {
    return this.element.tagName;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t3, i3 = this, s5, e4) {
    const o5 = this.strings;
    let n5 = false;
    if (void 0 === o5)
      t3 = S2(this, t3, i3, 0), n5 = !d2(t3) || t3 !== this._$AH && t3 !== T, n5 && (this._$AH = t3);
    else {
      const e5 = t3;
      let l4, h3;
      for (t3 = o5[0], l4 = 0; l4 < o5.length - 1; l4++)
        h3 = S2(this, e5[s5 + l4], i3, l4), h3 === T && (h3 = this._$AH[l4]), n5 || (n5 = !d2(h3) || h3 !== this._$AH[l4]), h3 === A ? t3 = A : t3 !== A && (t3 += (null != h3 ? h3 : "") + o5[l4 + 1]), this._$AH[l4] = h3;
    }
    n5 && !e4 && this.j(t3);
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
  constructor(t3, i3, s5, e4, o5) {
    super(t3, i3, s5, e4, o5), this.type = 5;
  }
  _$AI(t3, i3 = this) {
    var s5;
    if ((t3 = null !== (s5 = S2(this, t3, i3, 0)) && void 0 !== s5 ? s5 : A) === T)
      return;
    const e4 = this._$AH, o5 = t3 === A && e4 !== A || t3.capture !== e4.capture || t3.once !== e4.once || t3.passive !== e4.passive, n5 = t3 !== A && (e4 === A || o5);
    o5 && this.element.removeEventListener(this.name, this, e4), n5 && this.element.addEventListener(this.name, this, t3), this._$AH = t3;
  }
  handleEvent(t3) {
    var i3, s5;
    "function" == typeof this._$AH ? this._$AH.call(null !== (s5 = null === (i3 = this.options) || void 0 === i3 ? void 0 : i3.host) && void 0 !== s5 ? s5 : this.element, t3) : this._$AH.handleEvent(t3);
  }
};
var Z = class {
  constructor(t3, i3, s5) {
    this.element = t3, this.type = 6, this._$AN = void 0, this._$AM = i3, this.options = s5;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t3) {
    S2(this, t3);
  }
};
var B = i2.litHtmlPolyfillSupport;
null == B || B(N, R), (null !== (t2 = i2.litHtmlVersions) && void 0 !== t2 ? t2 : i2.litHtmlVersions = []).push("2.8.0");
var D = (t3, i3, s5) => {
  var e4, o5;
  const n5 = null !== (e4 = null == s5 ? void 0 : s5.renderBefore) && void 0 !== e4 ? e4 : i3;
  let l4 = n5._$litPart$;
  if (void 0 === l4) {
    const t4 = null !== (o5 = null == s5 ? void 0 : s5.renderBefore) && void 0 !== o5 ? o5 : null;
    n5._$litPart$ = l4 = new R(i3.insertBefore(u2(), t4), t4, void 0, null != s5 ? s5 : {});
  }
  return l4._$AI(t3), l4;
};

// node_modules/lit-element/lit-element.js
var l3;
var o4;
var s4 = class extends u {
  constructor() {
    super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
  }
  createRenderRoot() {
    var t3, e4;
    const i3 = super.createRenderRoot();
    return null !== (t3 = (e4 = this.renderOptions).renderBefore) && void 0 !== t3 || (e4.renderBefore = i3.firstChild), i3;
  }
  update(t3) {
    const i3 = this.render();
    this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(t3), this._$Do = D(i3, this.renderRoot, this.renderOptions);
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

// src/card.js
var SeloraAIDashboardCard = class extends s4 {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      // Automation suggestions
      _suggestions: { type: Array },
      _loadingSuggestions: { type: Boolean },
      _generatingSuggestions: { type: Boolean },
      // Automations list
      _automations: { type: Array },
      _loadingAutomations: { type: Boolean },
      // Expanded automation details
      _expandedId: { type: String },
      // New automation form
      _showNewAutomation: { type: Boolean },
      _newAutomationName: { type: String },
      // Error feedback
      _errorMessage: { type: String }
    };
  }
  constructor() {
    super();
    this.config = {};
    this._suggestions = [];
    this._loadingSuggestions = true;
    this._generatingSuggestions = false;
    this._automations = [];
    this._loadingAutomations = true;
    this._expandedId = null;
    this._showNewAutomation = false;
    this._newAutomationName = "";
    this._errorMessage = "";
  }
  setConfig(config) {
    this.config = config;
  }
  static getConfigElement() {
    return document.createElement("selora-ai-card-editor");
  }
  static getStubConfig() {
    return { title: "Selora AI", show_suggestions: true, show_automations: true, max_suggestions: 3, max_automations: 10 };
  }
  connectedCallback() {
    super.connectedCallback();
  }
  updated(changedProps) {
    if (changedProps.has("hass") && this.hass) {
      if (!this._initialLoaded) {
        this._initialLoaded = true;
        this._loadData();
      }
    }
  }
  async _loadData() {
    if (!this.hass)
      return;
    await Promise.all([
      this._loadSuggestions(),
      this._loadAutomations()
    ]);
  }
  // -------------------------------------------------------------------------
  // Data loaders
  // -------------------------------------------------------------------------
  async _loadSuggestions() {
    this._loadingSuggestions = true;
    try {
      const suggestions = await this.hass.callWS({ type: "selora_ai/get_suggestions" });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Selora AI Card: Failed to load suggestions", err);
      this._suggestions = [];
    } finally {
      this._loadingSuggestions = false;
    }
  }
  async _loadAutomations() {
    this._loadingAutomations = true;
    try {
      const automations = await this.hass.callWS({ type: "selora_ai/get_automations" });
      const max = this.config.max_automations || 10;
      this._automations = (automations || []).filter((a3) => a3.is_selora).sort((a3, b2) => {
        const aTime = a3.last_triggered || "";
        const bTime = b2.last_triggered || "";
        return bTime.localeCompare(aTime);
      }).slice(0, max);
    } catch (err) {
      console.error("Selora AI Card: Failed to load automations", err);
      this._automations = [];
    } finally {
      this._loadingAutomations = false;
    }
  }
  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------
  _showError(msg) {
    this._errorMessage = msg;
    setTimeout(() => {
      this._errorMessage = "";
    }, 5e3);
  }
  async _generateSuggestions() {
    this._generatingSuggestions = true;
    try {
      const suggestions = await this.hass.callWS({ type: "selora_ai/generate_suggestions" });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Selora AI Card: Failed to generate suggestions", err);
      this._showError("Failed to generate suggestions");
    } finally {
      this._generatingSuggestions = false;
    }
  }
  async _acceptSuggestion(suggestion) {
    try {
      const automationPayload = suggestion.automation_data || suggestion.automation || {
        alias: suggestion.alias,
        description: suggestion.description || "",
        trigger: suggestion.trigger || suggestion.triggers || [],
        action: suggestion.action || suggestion.actions || [],
        condition: suggestion.condition || suggestion.conditions || []
      };
      automationPayload.initial_state = automationPayload.initial_state ?? true;
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: automationPayload
      });
      this._suggestions = this._suggestions.filter((s5) => s5 !== suggestion);
      await this._loadAutomations();
    } catch (err) {
      console.error("Selora AI Card: Failed to accept suggestion", err);
      this._showError("Failed to accept suggestion");
    }
  }
  async _toggleAutomation(automation) {
    try {
      await this.hass.callWS({
        type: "selora_ai/toggle_automation",
        automation_id: automation.automation_id,
        entity_id: automation.entity_id
      });
      await this._loadAutomations();
    } catch (err) {
      console.error("Selora AI Card: Failed to toggle automation", err);
      this._showError("Failed to toggle automation");
    }
  }
  async _deleteAutomation(automation) {
    try {
      await this.hass.callWS({
        type: "selora_ai/soft_delete_automation",
        automation_id: automation.automation_id
      });
      await this._loadAutomations();
    } catch (err) {
      console.error("Selora AI Card: Failed to delete automation", err);
      this._showError("Failed to delete automation");
    }
  }
  _toggleExpanded(automationId) {
    this._expandedId = this._expandedId === automationId ? null : automationId;
  }
  _createInChat() {
    const name = this._newAutomationName.trim();
    if (!name)
      return;
    history.pushState(null, "", `/selora-ai-architect?new_automation=${encodeURIComponent(name)}`);
    window.dispatchEvent(new Event("location-changed"));
  }
  _openPanel() {
    history.pushState(null, "", "/selora-ai-architect?tab=automations");
    window.dispatchEvent(new Event("location-changed"));
  }
  // -------------------------------------------------------------------------
  // Formatting helpers
  // -------------------------------------------------------------------------
  _formatRelativeTime(dateStr) {
    if (!dateStr)
      return "Never";
    const date = new Date(dateStr);
    const now = /* @__PURE__ */ new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 6e4);
    if (diffMins < 1)
      return "Just now";
    if (diffMins < 60)
      return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24)
      return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7)
      return `${diffDays}d ago`;
    return date.toLocaleDateString();
  }
  _formatTrigger(t3) {
    if (!t3)
      return "Unknown trigger";
    if (t3.platform === "time" || t3.trigger === "time")
      return `Time: ${t3.at || ""}`;
    if (t3.platform === "state" || t3.trigger === "state")
      return `State: ${t3.entity_id || ""}`;
    if (t3.platform === "sun" || t3.trigger === "sun")
      return `Sun: ${t3.event || ""}`;
    return t3.platform || t3.trigger || "Trigger";
  }
  _formatAction(a3) {
    if (!a3)
      return "Unknown action";
    if (a3.service)
      return a3.service;
    if (a3.action)
      return a3.action;
    return "Action";
  }
  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  render() {
    const title = this.config.title || "Selora AI";
    const showSuggestions = this.config.show_suggestions !== false;
    const showAutomations = this.config.show_automations !== false;
    return x`
      <ha-card>
        <!-- Header -->
        <div class="card-header" @click=${this._openPanel}>
          <div class="header-left">
            <img src="/api/selora_ai/logo.png" alt="Selora" class="header-logo">
            <span class="header-title">${title}</span>
          </div>
          <ha-icon icon="mdi:open-in-new" class="header-action"></ha-icon>
        </div>

        <div class="card-content">
          <!-- Error banner -->
          ${this._errorMessage ? x`
            <div class="error-banner">
              <span>${this._errorMessage}</span>
              <ha-icon icon="mdi:close" class="error-dismiss" @click=${() => {
      this._errorMessage = "";
    }}></ha-icon>
            </div>
          ` : ""}

          <!-- Quick Actions -->
          <div class="section quick-actions">
            <button class="action-btn new-btn" @click=${() => {
      this._showNewAutomation = true;
    }}>
              <ha-icon icon="mdi:plus"></ha-icon>
              <span>New Automation</span>
            </button>
            <button class="action-btn suggest-btn"
              ?disabled=${this._generatingSuggestions}
              @click=${this._generateSuggestions}>
              ${this._generatingSuggestions ? x`<span class="spinner"></span>` : x`<ha-icon icon="mdi:auto-fix"></ha-icon>`}
              <span>${this._generatingSuggestions ? "Analyzing..." : "Generate Suggestions"}</span>
            </button>
          </div>

          <!-- Automation Suggestions -->
          ${showSuggestions ? this._renderSuggestions() : ""}

          <!-- Automations -->
          ${showAutomations ? this._renderAutomations() : ""}
        </div>
      </ha-card>

      <!-- Modal overlay for New Automation -->
      ${this._showNewAutomation ? x`
        <div class="modal-overlay" @click=${(e4) => {
      if (e4.target === e4.currentTarget)
        this._showNewAutomation = false;
    }}>
          <div class="modal">
            <div class="modal-title">New Automation</div>
            <div class="modal-label">Automation name</div>
            <div class="modal-row">
              <input
                class="modal-input"
                type="text"
                placeholder="e.g. Turn off lights at midnight"
                .value=${this._newAutomationName}
                @input=${(e4) => {
      this._newAutomationName = e4.target.value;
    }}
                @keydown=${(e4) => {
      if (e4.key === "Enter")
        this._createInChat();
    }}
              >
              <button class="modal-magic-btn" title="Let AI decide" @click=${this._createInChat}>
                <ha-icon icon="mdi:auto-fix"></ha-icon>
              </button>
            </div>
            <div class="modal-actions">
              <button class="modal-btn modal-cancel" @click=${() => {
      this._showNewAutomation = false;
    }}>Cancel</button>
              <button class="modal-btn modal-create" @click=${this._createInChat}
                ?disabled=${!this._newAutomationName.trim()}>
                <ha-icon icon="mdi:chat-plus-outline"></ha-icon> Create in Chat
              </button>
            </div>
          </div>
        </div>
      ` : ""}
    `;
  }
  _renderSuggestions() {
    const maxSuggestions = this.config.max_suggestions || 3;
    const suggestions = this._suggestions.slice(0, maxSuggestions);
    return x`
      <div class="section">
        <div class="section-header">
          <ha-icon icon="mdi:lightbulb-on-outline" class="section-icon"></ha-icon>
          <span>Suggestions</span>
          ${this._suggestions.length > 0 ? x`<span class="badge">${this._suggestions.length}</span>` : ""}
        </div>

        ${this._generatingSuggestions ? x`<div class="generating-row"><span class="dots-loader"><span></span><span></span><span></span></span> Generating suggestions...</div>` : this._loadingSuggestions ? x`<div class="loading-row"><span class="spinner"></span> Loading suggestions...</div>` : suggestions.length === 0 ? x`<div class="empty-row">No suggestions yet. Tap "Generate Suggestions" to analyze your home.</div>` : suggestions.map(
      (s5) => x`
                    <div class="suggestion-item">
                      <div class="suggestion-info">
                        <div class="suggestion-name">${s5.automation?.alias || s5.alias || "Untitled"}</div>
                        <div class="suggestion-desc">${s5.automation?.description || s5.description || ""}</div>
                      </div>
                      <button class="accept-btn" @click=${() => this._acceptSuggestion(s5)} title="Accept">
                        <ha-icon icon="mdi:check"></ha-icon>
                      </button>
                    </div>
                  `
    )}

        ${!this._loadingSuggestions && !this._generatingSuggestions && this._suggestions.length > maxSuggestions ? x`<div class="more-link" @click=${this._openPanel}>View all ${this._suggestions.length} suggestions</div>` : ""}
      </div>
    `;
  }
  _renderAutomations() {
    return x`
      <div class="section">
        <div class="section-header">
          <ha-icon icon="mdi:lightning-bolt" class="section-icon"></ha-icon>
          <span>Automations</span>
          ${this._automations.length > 0 ? x`<span class="badge">${this._automations.length}</span>` : ""}
        </div>

        ${this._loadingAutomations ? x`<div class="loading-row"><span class="spinner"></span> Loading automations...</div>` : this._automations.length === 0 ? x`<div class="empty-row">No Selora AI automations yet.</div>` : this._automations.map((a3) => this._renderAutomationItem(a3))}
      </div>
    `;
  }
  _renderAutomationItem(a3) {
    const isOn = a3.state === "on";
    const isExpanded = this._expandedId === a3.automation_id;
    const triggers = Array.isArray(a3.trigger) ? a3.trigger : [];
    const actions = Array.isArray(a3.action) ? a3.action : [];
    return x`
      <div class="automation-item ${isExpanded ? "expanded" : ""}">
        <div class="automation-row" @click=${() => this._toggleExpanded(a3.automation_id)}>
          <div class="activity-indicator ${isOn ? "active" : "inactive"}"></div>
          <div class="activity-info">
            <div class="activity-name">${a3.alias || a3.entity_id}</div>
            <div class="activity-meta">
              ${isOn ? "Enabled" : "Disabled"}
              ${a3.last_triggered ? x` · Ran ${this._formatRelativeTime(a3.last_triggered)}` : ""}
            </div>
          </div>
          <ha-icon
            icon=${isOn ? "mdi:toggle-switch" : "mdi:toggle-switch-off-outline"}
            class="activity-toggle ${isOn ? "on" : "off"}"
            @click=${(e4) => {
      e4.stopPropagation();
      this._toggleAutomation(a3);
    }}
          ></ha-icon>
        </div>

        ${isExpanded ? x`
          <div class="automation-details">
            ${a3.description ? x`<div class="detail-desc">${a3.description}</div>` : ""}

            ${triggers.length > 0 ? x`
              <div class="detail-section">
                <div class="detail-label">Triggers</div>
                ${triggers.map((t3) => x`
                  <div class="detail-chip trigger">${this._formatTrigger(t3)}</div>
                `)}
              </div>
            ` : ""}

            ${actions.length > 0 ? x`
              <div class="detail-section">
                <div class="detail-label">Actions</div>
                ${actions.map((act) => x`
                  <div class="detail-chip action">${this._formatAction(act)}</div>
                `)}
              </div>
            ` : ""}

            <div class="detail-actions">
              <button class="detail-btn open-btn" @click=${() => {
      history.pushState(null, "", "/selora-ai-architect?tab=automations");
      window.dispatchEvent(new Event("location-changed"));
    }}>
                <ha-icon icon="mdi:pencil-outline"></ha-icon> Edit in Panel
              </button>
              <button class="detail-btn delete-btn" @click=${() => this._deleteAutomation(a3)}>
                <ha-icon icon="mdi:trash-can-outline"></ha-icon> Delete
              </button>
            </div>
          </div>
        ` : ""}
      </div>
    `;
  }
  // -------------------------------------------------------------------------
  // Styles
  // -------------------------------------------------------------------------
  static get styles() {
    return i`
      :host {
        --selora-accent: #f59e0b;
      }

      ha-card {
        overflow: hidden;
      }

      /* ---- Header ---- */
      .card-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px;
        cursor: pointer;
        border-bottom: 1px solid var(--divider-color);
        transition: background 0.15s;
      }
      .card-header:hover {
        background: var(--secondary-background-color);
      }
      .header-left {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .header-logo {
        width: 26px;
        height: 26px;
        border-radius: 6px;
      }
      .header-title {
        font-size: 16px;
        font-weight: 600;
      }
      .header-action {
        --mdc-icon-size: 18px;
        opacity: 0.4;
        transition: opacity 0.15s;
      }
      .card-header:hover .header-action {
        opacity: 0.8;
      }

      /* ---- Content ---- */
      .card-content {
        padding: 12px 16px 16px;
      }

      /* ---- Quick Actions ---- */
      .quick-actions {
        display: flex;
        gap: 8px;
        margin-bottom: 16px;
      }
      .action-btn {
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 10px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.15s;
        font-family: inherit;
      }
      .action-btn:hover {
        background: rgba(245, 158, 11, 0.06);
        border-color: var(--selora-accent);
      }
      .action-btn:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .action-btn ha-icon {
        --mdc-icon-size: 18px;
      }
      .new-btn {
        background: var(--selora-accent);
        border-color: var(--selora-accent);
        color: #1a1a1a;
        font-weight: 600;
      }
      .new-btn:hover {
        background: #f7b731;
        border-color: #f7b731;
      }

      /* ---- Sections ---- */
      .section {
        margin-bottom: 12px;
      }
      .section:last-child {
        margin-bottom: 0;
      }
      .section-header {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        opacity: 0.7;
        margin-bottom: 8px;
        padding-bottom: 6px;
        border-bottom: 1px solid var(--divider-color);
      }
      .section-icon {
        --mdc-icon-size: 16px;
      }
      .badge {
        background: var(--selora-accent);
        color: white;
        font-size: 10px;
        font-weight: 700;
        padding: 1px 6px;
        border-radius: 10px;
        margin-left: auto;
      }

      /* ---- Suggestion Items ---- */
      .suggestion-item {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 0;
        border-bottom: 1px solid var(--divider-color);
      }
      .suggestion-item:last-child {
        border-bottom: none;
      }
      .suggestion-info {
        flex: 1;
        min-width: 0;
      }
      .suggestion-name {
        font-size: 13px;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .suggestion-desc {
        font-size: 11px;
        opacity: 0.6;
        margin-top: 2px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .accept-btn {
        flex-shrink: 0;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 1px solid var(--selora-accent);
        background: transparent;
        color: var(--selora-accent);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.15s;
      }
      .accept-btn:hover {
        background: var(--selora-accent);
        color: white;
      }
      .accept-btn ha-icon {
        --mdc-icon-size: 18px;
      }

      /* ---- Automation Items ---- */
      .automation-item {
        border-bottom: 1px solid var(--divider-color);
      }
      .automation-item:last-child {
        border-bottom: none;
      }
      .automation-item.expanded {
        background: rgba(245, 158, 11, 0.04);
        border-radius: 8px;
        margin: 4px -8px;
        padding: 0 8px;
        border-bottom: none;
      }
      .automation-row {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 0;
        cursor: pointer;
      }
      .activity-indicator {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .activity-indicator.active {
        background: var(--selora-accent);
        box-shadow: 0 0 6px rgba(245, 158, 11, 0.5);
      }
      .activity-indicator.inactive {
        background: var(--disabled-text-color, #999);
      }
      .activity-info {
        flex: 1;
        min-width: 0;
      }
      .activity-name {
        font-size: 13px;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .activity-meta {
        font-size: 11px;
        opacity: 0.5;
        margin-top: 1px;
      }
      .activity-toggle {
        --mdc-icon-size: 24px;
        cursor: pointer;
        flex-shrink: 0;
        transition: color 0.15s;
      }
      .activity-toggle.on {
        color: var(--selora-accent);
      }
      .activity-toggle.off {
        color: var(--disabled-text-color, #999);
      }

      /* ---- Expanded Details ---- */
      .automation-details {
        padding: 4px 0 10px 18px;
      }
      .detail-desc {
        font-size: 12px;
        opacity: 0.6;
        margin-bottom: 8px;
        font-style: italic;
      }
      .detail-section {
        margin-bottom: 6px;
      }
      .detail-label {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        opacity: 0.5;
        margin-bottom: 3px;
      }
      .detail-chip {
        display: inline-block;
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 4px;
        margin: 2px 4px 2px 0;
      }
      .detail-chip.trigger {
        background: rgba(245, 158, 11, 0.12);
        border: 1px solid var(--selora-accent);
        color: var(--primary-text-color);
      }
      .detail-chip.action {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
      }
      .detail-actions {
        display: flex;
        gap: 8px;
        margin-top: 8px;
      }
      .detail-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 5px 10px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        border: 1px solid var(--divider-color);
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-family: inherit;
        transition: all 0.15s;
      }
      .detail-btn ha-icon {
        --mdc-icon-size: 14px;
      }
      .open-btn:hover {
        border-color: var(--selora-accent);
        color: var(--selora-accent);
      }
      .delete-btn:hover {
        border-color: var(--error-color, #f44336);
        color: var(--error-color, #f44336);
      }

      /* ---- Error banner ---- */
      .error-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px 12px;
        margin-bottom: 12px;
        border-radius: 8px;
        background: rgba(244, 67, 54, 0.1);
        border: 1px solid var(--error-color, #f44336);
        color: var(--error-color, #f44336);
        font-size: 12px;
        font-weight: 500;
      }
      .error-dismiss {
        --mdc-icon-size: 16px;
        cursor: pointer;
        opacity: 0.7;
        flex-shrink: 0;
      }
      .error-dismiss:hover {
        opacity: 1;
      }

      /* ---- Common ---- */
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
      .more-link {
        text-align: center;
        font-size: 12px;
        color: var(--selora-accent);
        cursor: pointer;
        padding: 8px 0 4px;
        font-weight: 500;
      }
      .more-link:hover {
        text-decoration: underline;
      }

      /* ---- Bouncing dots loader ---- */
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
      .dots-loader span:nth-child(2) { animation-delay: 0.2s; }
      .dots-loader span:nth-child(3) { animation-delay: 0.4s; }
      @keyframes bounce {
        0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
        30% { transform: translateY(-6px); opacity: 1; }
      }

      /* ---- Spinner (fallback) ---- */
      .spinner {
        display: inline-block;
        width: 16px;
        height: 16px;
        border: 2px solid transparent;
        border-top-color: var(--selora-accent);
        border-left-color: var(--selora-accent);
        border-radius: 50%;
        animation: spin 0.6s linear infinite;
      }
      @keyframes spin {
        to { transform: rotate(360deg); }
      }

      /* ---- Generating row ---- */
      .generating-row {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 0;
        font-size: 12px;
        opacity: 0.7;
      }

      /* ---- Modal overlay (matches panel) ---- */
      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10001;
      }
      .modal {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
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
        border-color: #f59e0b;
        color: #f59e0b;
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
        transition: background 0.15s, opacity 0.15s;
        user-select: none;
        letter-spacing: 0.02em;
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
        border-color: #f59e0b;
        color: #f59e0b;
      }
      .modal-create {
        background: #f59e0b;
        border-color: #f59e0b;
        color: #1a1a1a;
      }
      .modal-create:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
    `;
  }
  getCardSize() {
    return 4;
  }
};
var SeloraAICardEditor = class extends s4 {
  static get properties() {
    return {
      hass: { type: Object },
      _config: { type: Object }
    };
  }
  setConfig(config) {
    this._config = config;
  }
  _valueChanged(key, value) {
    const newConfig = { ...this._config, [key]: value };
    this._config = newConfig;
    const event = new CustomEvent("config-changed", { detail: { config: newConfig } });
    this.dispatchEvent(event);
  }
  render() {
    if (!this._config)
      return x``;
    return x`
      <div style="padding: 16px;">
        <ha-textfield
          label="Title"
          .value=${this._config.title || "Selora AI"}
          @change=${(e4) => this._valueChanged("title", e4.target.value)}
        ></ha-textfield>
        <ha-formfield label="Show Suggestions">
          <ha-switch
            .checked=${this._config.show_suggestions !== false}
            @change=${(e4) => this._valueChanged("show_suggestions", e4.target.checked)}
          ></ha-switch>
        </ha-formfield>
        <ha-formfield label="Show Automations">
          <ha-switch
            .checked=${this._config.show_automations !== false}
            @change=${(e4) => this._valueChanged("show_automations", e4.target.checked)}
          ></ha-switch>
        </ha-formfield>
        <ha-textfield
          label="Max Suggestions"
          type="number"
          .value=${String(this._config.max_suggestions || 3)}
          @change=${(e4) => this._valueChanged("max_suggestions", parseInt(e4.target.value, 10))}
        ></ha-textfield>
        <ha-textfield
          label="Max Automations"
          type="number"
          .value=${String(this._config.max_automations || 10)}
          @change=${(e4) => this._valueChanged("max_automations", parseInt(e4.target.value, 10))}
        ></ha-textfield>
      </div>
    `;
  }
};
customElements.define("selora-ai-card", SeloraAIDashboardCard);
customElements.define("selora-ai-card-editor", SeloraAICardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "selora-ai-card",
  name: "Selora AI",
  description: "Dashboard card for Selora AI automation suggestions, quick chat, and activity feed.",
  preview: true
});
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
*/
