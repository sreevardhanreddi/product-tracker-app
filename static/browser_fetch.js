// Browser-side fallback for Shopify stores that rate-limit the server.
//
// Shopify serves /products/{handle}.js with Access-Control-Allow-Origin: *,
// so the user's browser can read it directly (from a different IP than the
// server). Other platforms send no CORS headers (and Amazon/Pepperfry
// bot-block anyway), so there's no browser fallback for them.

function shopifyJsUrl(productUrl) {
  const url = new URL(productUrl);
  return `${url.origin}${url.pathname.replace(/\/+$/, "")}.js`;
}

async function fetchShopifyProductInBrowser(productUrl) {
  const res = await fetch(shopifyJsUrl(productUrl), { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} from ${shopifyJsUrl(productUrl)}`);
  const data = await res.json();
  if (!Array.isArray(data?.variants)) throw new Error("Not a Shopify product .js response");
  return data;
}

// True if the browser can read the URL as a Shopify product.
async function isShopifyInBrowser(productUrl) {
  try {
    await fetchShopifyProductInBrowser(productUrl);
    return true;
  } catch {
    return false;
  }
}

async function fetchShopifyPriceInBrowser(link) {
  const variantId = new URL(link.url).searchParams.get("variant");
  const data = await fetchShopifyProductInBrowser(link.url);
  const variants = data.variants;
  const variant = (variantId && variants.find((v) => String(v.id) === variantId)) || variants[0];
  if (!variant) throw new Error("No variants in Shopify .js response");
  // .js prices are in minor units (paise): 139900 => 1399.00
  const price = Number(variant.price) / 100;
  const image = variant.featured_image?.src || data.featured_image || null;
  return {
    name: data.title,
    price,
    currency: link.currency || "INR",
    in_stock: Boolean(variant.available),
    image_url: image && image.startsWith("//") ? `https:${image}` : image,
    raw_price_text: String(variant.price),
  };
}

async function syncLinkFromBrowser(productId, link) {
  if (link?.platform !== "shopify") {
    throw new Error(`No browser fallback for platform "${link?.platform}" (no CORS)`);
  }
  const scraped = await fetchShopifyPriceInBrowser(link);
  console.log("Browser fetch :", scraped);
  const res = await fetch(`/api/products/${productId}/links/${link.id}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scraped),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(`Sync rejected: ${err.detail || res.status}`);
  }
  return res.json();
}

// POST a new product/link. If the server can't detect the platform (a
// throttled Shopify store answers its probes with 429) but the browser can
// read the URL as Shopify, retry once with a platform hint.
async function postTrackedUrl(endpoint, payload) {
  const send = (body) =>
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  const res = await send(payload);
  if (res.status !== 422 || !payload.url) return res;
  if (!(await isShopifyInBrowser(payload.url))) return res;
  console.log("Server couldn't detect platform; browser confirmed Shopify for", payload.url);
  return send({ ...payload, platform: "shopify" });
}

// After adding, give the server's queued scrape a chance; any Shopify link
// still without a price is then fetched by the browser and synced.
// Returns the refreshed product, or null if nothing needed populating.
async function populateShopifyLinksFromBrowser(productId, { waitMs = 20000, pollMs = 4000 } = {}) {
  const getProduct = async () => (await fetch(`/api/products/${productId}`)).json();
  const pending = (p) => (p.links || []).filter((l) => l.platform === "shopify" && l.current_price == null);

  let product = await getProduct();
  for (let waited = 0; pending(product).length && waited < waitMs; waited += pollMs) {
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    product = await getProduct();
  }

  const links = pending(product);
  if (!links.length) return null;
  for (const link of links) {
    try {
      product = await syncLinkFromBrowser(productId, link);
    } catch (err) {
      console.error(`Browser populate failed for link ${link.id}:`, err);
    }
  }
  return product;
}
