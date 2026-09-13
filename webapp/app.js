let tg = window.Telegram.WebApp;
tg.expand(); // Expand to full height

let catalog = [];
let cart = {}; // { id: { item, qty } }
let isWholesale = false; // We can get this from backend if we want

const DOM = {
    search: document.getElementById('search-input'),
    catalogList: document.getElementById('catalog-list'),
    loading: document.getElementById('loading'),
    viewCatalog: document.getElementById('view-catalog'),
    viewCart: document.getElementById('view-cart'),
    viewSuccess: document.getElementById('view-success'),
    btnCart: document.getElementById('btn-cart'),
    cartBadge: document.getElementById('cart-badge'),
    headerTitle: document.getElementById('header-title'),
    cartItems: document.getElementById('cart-items'),
    cartTotal: document.getElementById('cart-total'),
    
    // Form
    deliveryMethod: document.getElementById('delivery_method'),
    deliveryAddressWrapper: document.getElementById('delivery_address_wrapper'),
    deliveryAddress: document.getElementById('delivery_address'),
    contact: document.getElementById('contact'),
    paymentMethod: document.getElementById('payment_method'),
    notes: document.getElementById('notes')
};

// Listeners
DOM.search.addEventListener('input', debounce(loadCatalog, 300));
DOM.btnCart.addEventListener('click', toggleCart);
DOM.deliveryMethod.addEventListener('change', (e) => {
    if (e.target.value !== 'pickup') {
        DOM.deliveryAddressWrapper.style.display = 'block';
    } else {
        DOM.deliveryAddressWrapper.style.display = 'none';
    }
});

// Setup TG Main Button
tg.MainButton.textColor = '#ffffff';
tg.MainButton.color = '#2ce566';

// Toggle Views
let currentView = 'catalog';

function toggleCart() {
    if (currentView === 'catalog') {
        showView('cart');
        DOM.headerTitle.innerText = "Оформление";
        DOM.btnCart.style.display = 'none';
        renderCart();
        
        tg.MainButton.text = "ОФОРМИТЬ ЗАКАЗ";
        tg.MainButton.show();
        tg.onEvent('mainButtonClicked', submitOrder);
        tg.BackButton.show();
        tg.onEvent('backButtonClicked', showCatalog);
    }
}

function showCatalog() {
    showView('catalog');
    DOM.headerTitle.innerText = "Каталог";
    updateCartBadge();
    
    tg.MainButton.hide();
    tg.MainButton.offClick(submitOrder);
    tg.BackButton.hide();
    tg.offEvent('backButtonClicked', showCatalog);
}

function showView(viewId) {
    currentView = viewId;
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-' + viewId).classList.add('active');
}

// Fetch Data
async function loadCatalog() {
    DOM.loading.style.display = 'block';
    DOM.catalogList.innerHTML = '';
    
    let query = DOM.search.value;
    try {
        let res = await fetch(`/api/catalog?q=${encodeURIComponent(query)}&limit=100`);
        let data = await res.json();
        
        if (data.ok) {
            catalog = data.parts;
            renderCatalog();
        }
    } catch (e) {
        console.error(e);
        tg.showAlert("Ошибка при загрузке каталога");
    } finally {
        DOM.loading.style.display = 'none';
    }
}

function renderCatalog() {
    DOM.catalogList.innerHTML = '';
    
    if (catalog.length === 0) {
        DOM.catalogList.innerHTML = '<p style="text-align:center;color:var(--hint-color);">Ничего не найдено</p>';
        return;
    }
    
    catalog.forEach(part => {
        const card = document.createElement('div');
        card.className = 'part-card';
        
        // Use retail price by default. (Logic to switch to wholesale could be added if user is wholesale)
        const price = part.retail_price;
        
        const cartItem = cart[part.id];
        const qty = cartItem ? cartItem.qty : 0;
        
        let controlsHTML = '';
        if (qty > 0) {
            controlsHTML = `
                <div class="quantity-controls">
                    <button class="qty-btn" onclick="updateCart(${part.id}, -1)">-</button>
                    <span>${qty}</span>
                    <button class="qty-btn" onclick="updateCart(${part.id}, 1)">+</button>
                </div>
            `;
        } else {
            controlsHTML = `<button class="add-to-cart-btn" onclick="updateCart(${part.id}, 1)">Добавить</button>`;
        }
        
        card.innerHTML = `
            <div class="part-title">${part.name}</div>
            <div class="part-meta">${part.category} > ${part.subcategory}</div>
            <div class="part-meta">Остаток: ${part.quantity} шт.</div>
            <div class="part-price-row">
                <div class="price">${price} ₽</div>
                ${controlsHTML}
            </div>
        `;
        DOM.catalogList.appendChild(card);
    });
}

window.updateCart = function(id, delta) {
    const part = catalog.find(p => p.id === id);
    if (!part) return;
    
    if (!cart[id]) {
        cart[id] = { item: part, qty: 0 };
    }
    
    cart[id].qty += delta;
    
    if (cart[id].qty > part.quantity) {
        cart[id].qty = part.quantity;
        tg.showAlert("Недостаточно на складе!");
    }
    
    if (cart[id].qty <= 0) {
        delete cart[id];
    }
    
    // If we are in cart view, render cart, otherwise render catalog to update buttons
    if (currentView === 'catalog') {
        renderCatalog();
        updateCartBadge();
    } else if (currentView === 'cart') {
        renderCart();
    }
};

function updateCartBadge() {
    const count = Object.keys(cart).length;
    if (count > 0) {
        DOM.btnCart.style.display = 'block';
        DOM.cartBadge.innerText = count;
    } else {
        DOM.btnCart.style.display = 'none';
    }
}

function renderCart() {
    DOM.cartItems.innerHTML = '';
    let total = 0;
    
    const ids = Object.keys(cart);
    if (ids.length === 0) {
        DOM.cartItems.innerHTML = '<p>Корзина пуста</p>';
        DOM.cartTotal.innerText = '0 ₽';
        tg.MainButton.hide();
        return;
    }
    
    ids.forEach(id => {
        const c = cart[id];
        const price = c.item.retail_price;
        const sum = price * c.qty;
        total += sum;
        
        const el = document.createElement('div');
        el.className = 'cart-item';
        el.innerHTML = `
            <div class="cart-item-info">
                <h4>${c.item.name}</h4>
                <p>${price} ₽ × ${c.qty} шт. = <strong>${sum} ₽</strong></p>
            </div>
            <div class="quantity-controls">
                <button class="qty-btn" onclick="updateCart(${id}, -1)">-</button>
                <span>${c.qty}</span>
                <button class="qty-btn" onclick="updateCart(${id}, 1)">+</button>
            </div>
        `;
        DOM.cartItems.appendChild(el);
    });
    
    DOM.cartTotal.innerText = `${total} ₽`;
    tg.MainButton.show();
}

async function submitOrder() {
    let items = Object.values(cart).map(c => ({
        id: c.item.id,
        quantity: c.qty,
        price: c.item.retail_price
    }));
    
    if (items.length === 0) return;
    
    let userId = tg.initDataUnsafe?.user?.id || 0;
    let userName = tg.initDataUnsafe?.user?.first_name || "Web Client";
    
    let contact = DOM.contact.value.trim();
    if (!contact) {
        tg.showAlert("Укажите контактный телефон");
        return;
    }
    
    let delivery = DOM.deliveryMethod.value;
    let address = DOM.deliveryAddress.value.trim();
    if (delivery !== 'pickup' && !address) {
        tg.showAlert("Укажите адрес доставки");
        return;
    }

    tg.MainButton.showProgress();
    
    let payload = {
        user_id: userId,
        user_name: userName,
        contact: contact,
        delivery_method: delivery,
        delivery_address: address,
        payment_method: DOM.paymentMethod.value,
        notes: DOM.notes.value.trim(),
        items: items
    };

    try {
        let res = await fetch('/api/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        let data = await res.json();
        if (data.ok) {
            cart = {};
            showView('success');
            document.getElementById('success-order-id').innerText = data.order_id;
            DOM.headerTitle.innerText = "Готово";
            tg.MainButton.hide();
            tg.BackButton.hide();
            tg.MainButton.offClick(submitOrder);
            
            // Allow user to close WebApp
            setTimeout(() => {
                tg.close();
            }, 5000);
        } else {
            tg.showAlert("Ошибка: " + data.error);
        }
    } catch (e) {
        console.error(e);
        tg.showAlert("Сетевая ошибка при отправке");
    } finally {
        tg.MainButton.hideProgress();
    }
}

// Utils
function debounce(func, wait) {
    let timeout;
    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}

// Init
loadCatalog();
