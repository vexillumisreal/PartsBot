const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

const State = {
    view: 'catalog', // 'catalog', 'cart', 'success'
    level: 'brands', // 'brands', 'models', 'parts'
    currentBrand: null,
    currentModel: null,
    searchQuery: '',
    cart: [],
    userStatus: 'retail' // default, will be fetched
};

// DOM Elements
const els = {
    viewCatalog: document.getElementById('view-catalog'),
    viewCart: document.getElementById('view-cart'),
    viewSuccess: document.getElementById('view-success'),
    catalogList: document.getElementById('catalog-list'),
    loading: document.getElementById('loading'),
    btnCart: document.getElementById('btn-cart'),
    cartBadge: document.getElementById('cart-badge'),
    btnBack: document.getElementById('btn-back'),
    headerTitle: document.getElementById('header-title'),
    searchContainer: document.getElementById('search-container'),
    searchInput: document.getElementById('search-input'),
    btnClearSearch: document.getElementById('btn-clear-search'),
    breadcrumbs: document.getElementById('breadcrumbs'),
    deliveryMethod: document.getElementById('delivery_method'),
    deliveryAddressWrapper: document.getElementById('delivery_address_wrapper')
};

// Initialize
async function init() {
    setupEventListeners();
    await fetchUserStatus();
    await loadBrands();
}

function setupEventListeners() {
    els.btnCart.addEventListener('click', () => {
        if (State.view === 'catalog') {
            showCart();
        } else {
            showCatalog();
        }
    });

    els.btnBack.addEventListener('click', goBack);

    els.searchInput.addEventListener('input', (e) => {
        State.searchQuery = e.target.value.toLowerCase().trim();
        els.btnClearSearch.style.display = State.searchQuery ? 'block' : 'none';
        
        // Use debounce in a real app, keeping simple here
        if (State.searchTimeout) clearTimeout(State.searchTimeout);
        State.searchTimeout = setTimeout(() => {
            if (State.level === 'parts') {
                renderParts(); // local filter
            } else if (State.searchQuery.length >= 3) {
                // If searching from brands/models, we might want to do a global search.
                // For now, we will just restrict local search to parts view.
                // Or we can fetch global parts. Let's do a global part search if length >= 3
                searchGlobalParts(State.searchQuery);
            } else if (State.searchQuery.length === 0) {
                // Return to current level
                if (State.level === 'brands') loadBrands();
                else if (State.level === 'models') loadModels(State.currentBrand);
            }
        }, 300);
    });

    els.btnClearSearch.addEventListener('click', () => {
        els.searchInput.value = '';
        State.searchQuery = '';
        els.btnClearSearch.style.display = 'none';
        if (State.level === 'brands') loadBrands();
        else if (State.level === 'models') loadModels(State.currentBrand);
        else if (State.level === 'parts') renderParts();
    });

    els.deliveryMethod.addEventListener('change', (e) => {
        els.deliveryAddressWrapper.style.display = e.target.value === 'pickup' ? 'none' : 'block';
    });

    tg.onEvent('mainButtonClicked', submitOrder);
}

// Navigation & Routing
function updateHeader() {
    if (State.view === 'cart') {
        els.headerTitle.textContent = 'Корзина';
        els.btnBack.style.display = 'flex';
        els.btnCart.style.display = 'none';
        els.searchContainer.style.display = 'none';
        els.breadcrumbs.style.display = 'none';
        tg.MainButton.text = "ОФОРМИТЬ ЗАКАЗ";
        tg.MainButton.show();
    } else if (State.view === 'success') {
        els.headerTitle.textContent = 'Успешно';
        els.btnBack.style.display = 'none';
        els.btnCart.style.display = 'none';
        els.searchContainer.style.display = 'none';
        els.breadcrumbs.style.display = 'none';
        tg.MainButton.hide();
    } else {
        els.btnCart.style.display = 'flex';
        els.searchContainer.style.display = 'block';
        updateCartBadge();
        
        if (State.level === 'brands') {
            els.headerTitle.textContent = 'Каталог';
            els.btnBack.style.display = 'none';
            els.breadcrumbs.style.display = 'none';
        } else {
            els.btnBack.style.display = 'flex';
            els.headerTitle.textContent = State.level === 'models' ? State.currentBrand : State.currentModel;
            renderBreadcrumbs();
        }
        
        tg.MainButton.hide();
    }
}

function renderBreadcrumbs() {
    els.breadcrumbs.style.display = 'flex';
    let html = `<span class="breadcrumb-item" onclick="loadBrands()">Каталог</span>`;
    
    if (State.currentBrand) {
        html += `<span class="breadcrumb-separator">›</span>`;
        if (State.level === 'models') {
            html += `<span class="breadcrumb-current">${State.currentBrand}</span>`;
        } else {
            html += `<span class="breadcrumb-item" onclick="loadModels('${State.currentBrand}')">${State.currentBrand}</span>`;
        }
    }
    
    if (State.currentModel && State.level === 'parts') {
        html += `<span class="breadcrumb-separator">›</span>`;
        html += `<span class="breadcrumb-current">${State.currentModel}</span>`;
    }
    
    els.breadcrumbs.innerHTML = html;
}

function goBack() {
    if (State.view === 'cart') {
        showCatalog();
    } else if (State.view === 'catalog') {
        if (State.level === 'parts') {
            loadModels(State.currentBrand);
        } else if (State.level === 'models') {
            loadBrands();
        }
    }
}

function showCatalog() {
    State.view = 'catalog';
    els.viewCatalog.classList.add('active');
    els.viewCart.classList.remove('active');
    els.viewSuccess.classList.remove('active');
    updateHeader();
}

function showCart() {
    State.view = 'cart';
    els.viewCatalog.classList.remove('active');
    els.viewCart.classList.add('active');
    els.viewSuccess.classList.remove('active');
    renderCart();
    updateHeader();
}

function showSuccess(orderId) {
    State.view = 'success';
    els.viewCatalog.classList.remove('active');
    els.viewCart.classList.remove('active');
    els.viewSuccess.classList.add('active');
    document.getElementById('success-order-id').textContent = orderId;
    updateHeader();
    
    // Clear cart
    State.cart = [];
    saveCart();
    updateCartBadge();
}

// Data Fetching
async function fetchUserStatus() {
    try {
        const userId = tg.initDataUnsafe?.user?.id || 0;
        const res = await fetch(`/api/user_status?user_id=${userId}`);
        const data = await res.json();
        if (data.ok) State.userStatus = data.status;
    } catch (e) {
        console.error('Failed to fetch user status', e);
    }
}

function showLoading(show) {
    els.loading.style.display = show ? 'flex' : 'none';
    if (show) els.catalogList.innerHTML = '';
}

async function loadBrands() {
    State.level = 'brands';
    State.currentBrand = null;
    State.currentModel = null;
    els.searchInput.value = '';
    State.searchQuery = '';
    els.btnClearSearch.style.display = 'none';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch('/api/brands');
        const data = await res.json();
        if (data.ok) {
            renderBrands(data.brands);
        }
    } catch (e) {
        console.error(e);
        els.catalogList.innerHTML = '<div class="empty-state">Ошибка загрузки.</div>';
    }
    showLoading(false);
}

async function loadModels(brand) {
    State.level = 'models';
    State.currentBrand = brand;
    State.currentModel = null;
    els.searchInput.value = '';
    State.searchQuery = '';
    els.btnClearSearch.style.display = 'none';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch(`/api/models?brand=${encodeURIComponent(brand)}`);
        const data = await res.json();
        if (data.ok) {
            renderModels(data.models);
        }
    } catch (e) {
        console.error(e);
    }
    showLoading(false);
}

async function loadParts(brand, model) {
    State.level = 'parts';
    State.currentBrand = brand;
    State.currentModel = model;
    els.searchInput.value = '';
    State.searchQuery = '';
    els.btnClearSearch.style.display = 'none';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch(`/api/parts?brand=${encodeURIComponent(brand)}&model=${encodeURIComponent(model)}`);
        const data = await res.json();
        if (data.ok) {
            State.currentParts = data.parts; // cache for local search
            renderParts();
        }
    } catch (e) {
        console.error(e);
    }
    showLoading(false);
}

async function searchGlobalParts(query) {
    State.level = 'search';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch(`/api/catalog?q=${encodeURIComponent(query)}&page=1&limit=100`);
        const data = await res.json();
        if (data.ok) {
            State.currentParts = data.parts;
            renderParts(true); // true = show brand/model context
        }
    } catch (e) {
        console.error(e);
    }
    showLoading(false);
}

// Rendering
function renderBrands(brands) {
    if (brands.length === 0) {
        els.catalogList.innerHTML = '<div class="empty-state">Бренды не найдены</div>';
        return;
    }
    
    els.catalogList.innerHTML = brands.map(b => `
        <div class="brand-card" onclick="loadModels('${b.name}')">
            <h3 class="card-title">${b.name}</h3>
            <span class="card-subtitle">${b.count} моделей</span>
        </div>
    `).join('');
}

function renderModels(models) {
    if (models.length === 0) {
        els.catalogList.innerHTML = '<div class="empty-state">Модели не найдены</div>';
        return;
    }
    
    els.catalogList.innerHTML = models.map(m => `
        <div class="model-card" onclick="loadParts('${State.currentBrand}', '${m.name}')">
            <h3 class="card-title">${m.name}</h3>
            <span class="card-subtitle">${m.count} запчастей</span>
        </div>
    `).join('');
}

function renderParts(showContext = false) {
    let parts = State.currentParts || [];
    
    if (State.searchQuery) {
        parts = parts.filter(p => p.name.toLowerCase().includes(State.searchQuery));
    }

    if (parts.length === 0) {
        els.catalogList.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <p>Ничего не найдено</p>
            </div>
        `;
        return;
    }

    els.catalogList.innerHTML = parts.map(p => {
        const price = State.userStatus === 'wholesale' ? p.wholesale_price : p.retail_price;
        const retailHtml = State.userStatus === 'wholesale' ? `<span class="retail-price">${p.retail_price} ₽</span>` : '';
        const cartItem = State.cart.find(item => item.id === p.id);
        const contextHtml = showContext ? `<div class="part-meta">${p.category} &gt; ${p.subcategory}</div>` : '';
        
        return `
            <div class="part-card">
                <div>
                    <h3 class="part-name">${p.name}</h3>
                    ${contextHtml}
                    <div class="part-meta">В наличии: ${p.quantity} шт.</div>
                </div>
                <div class="part-price-row">
                    <div class="part-price">${price} ₽${retailHtml}</div>
                </div>
                ${cartItem 
                    ? `<div class="quantity-control">
                        <button class="qty-btn" onclick="updateQty(${p.id}, -1)">-</button>
                        <span class="qty-val">${cartItem.qty}</span>
                        <button class="qty-btn" onclick="updateQty(${p.id}, 1)">+</button>
                       </div>`
                    : `<button class="add-to-cart-btn" ${p.quantity === 0 ? 'disabled' : ''} onclick='addToCart(${JSON.stringify(p).replace(/'/g, "&apos;")})'>
                        В корзину
                       </button>`
                }
            </div>
        `;
    }).join('');
}

// Cart Logic
function addToCart(part) {
    if (part.quantity <= 0) return;
    State.cart.push({ ...part, qty: 1 });
    saveCart();
    renderParts(State.level === 'search');
    updateCartBadge();
    tg.HapticFeedback.impactOccurred('light');
}

function updateQty(id, delta) {
    const item = State.cart.find(i => i.id === id);
    if (!item) return;
    
    item.qty += delta;
    
    if (item.qty > item.quantity) {
        item.qty = item.quantity;
        tg.HapticFeedback.notificationOccurred('warning');
    } else if (item.qty <= 0) {
        State.cart = State.cart.filter(i => i.id !== id);
    } else {
        tg.HapticFeedback.impactOccurred('light');
    }
    
    saveCart();
    if (State.view === 'cart') renderCart();
    else renderParts(State.level === 'search');
    updateCartBadge();
}

function updateCartBadge() {
    const totalQty = State.cart.reduce((sum, item) => sum + item.qty, 0);
    els.cartBadge.textContent = totalQty;
    els.cartBadge.style.display = totalQty > 0 ? 'block' : 'none';
}

function saveCart() {
    try {
        localStorage.setItem('partsbot_cart', JSON.stringify(State.cart));
    } catch (e) {}
}

function loadCart() {
    try {
        const saved = localStorage.getItem('partsbot_cart');
        if (saved) State.cart = JSON.parse(saved);
        updateCartBadge();
    } catch (e) {}
}

function renderCart() {
    const cartList = document.getElementById('cart-items');
    
    if (State.cart.length === 0) {
        cartList.innerHTML = `
            <div class="empty-state">
                <p>Корзина пуста</p>
                <button class="add-to-cart-btn" style="width: auto; margin-top: 16px;" onclick="showCatalog()">Перейти в каталог</button>
            </div>
        `;
        document.querySelector('.checkout-form').style.display = 'none';
        document.getElementById('cart-total').textContent = '0 ₽';
        tg.MainButton.hide();
        return;
    }
    
    document.querySelector('.checkout-form').style.display = 'block';
    
    let total = 0;
    cartList.innerHTML = State.cart.map(item => {
        const price = State.userStatus === 'wholesale' ? item.wholesale_price : item.retail_price;
        total += price * item.qty;
        
        return `
            <div class="cart-item glass-panel">
                <div class="cart-item-details">
                    <div class="cart-item-name">${item.name}</div>
                    <div class="cart-item-price">${price} ₽</div>
                </div>
                <div class="cart-item-actions">
                    <div class="quantity-control" style="width: 100px;">
                        <button class="qty-btn" onclick="updateQty(${item.id}, -1)">-</button>
                        <span class="qty-val">${item.qty}</span>
                        <button class="qty-btn" onclick="updateQty(${item.id}, 1)">+</button>
                    </div>
                    <button class="remove-btn icon-btn" onclick="updateQty(${item.id}, -${item.qty})">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>
        `;
    }).join('');
    
    document.getElementById('cart-total').textContent = `${total} ₽`;
    tg.MainButton.text = `ОФОРМИТЬ ЗАКАЗ НА ${total} ₽`;
    tg.MainButton.show();
}

// Order Submission
async function submitOrder() {
    if (State.cart.length === 0) return;
    
    const deliveryMethod = els.deliveryMethod.value;
    const deliveryAddress = document.getElementById('delivery_address').value;
    const contact = document.getElementById('contact').value;
    const paymentMethod = document.getElementById('payment_method').value;
    const notes = document.getElementById('notes').value;
    
    if (!contact) {
        tg.showAlert('Пожалуйста, укажите контактный телефон.');
        return;
    }
    
    if (deliveryMethod !== 'pickup' && !deliveryAddress) {
        tg.showAlert('Пожалуйста, укажите адрес доставки.');
        return;
    }
    
    tg.MainButton.showProgress();
    
    const orderData = {
        user_id: tg.initDataUnsafe?.user?.id || 0,
        username: tg.initDataUnsafe?.user?.username || '',
        first_name: tg.initDataUnsafe?.user?.first_name || '',
        items: State.cart,
        delivery_method: deliveryMethod,
        delivery_address: deliveryAddress,
        contact: contact,
        payment_method: paymentMethod,
        notes: notes
    };
    
    try {
        const response = await fetch('/api/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(orderData)
        });
        
        const result = await response.json();
        
        if (result.ok) {
            tg.HapticFeedback.notificationOccurred('success');
            showSuccess(result.order_id);
        } else {
            tg.showAlert('Ошибка: ' + (result.error || 'Не удалось оформить заказ.'));
        }
    } catch (e) {
        tg.showAlert('Ошибка сети. Попробуйте позже.');
    } finally {
        tg.MainButton.hideProgress();
    }
}

// Start
loadCart();
init();
