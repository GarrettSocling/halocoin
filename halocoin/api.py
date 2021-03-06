import json
import os
import tempfile
import threading

import jinja2
from flask import Flask, request, Response, render_template, send_file
from flask_socketio import SocketIO

from halocoin import tools, engine
from halocoin.blockchain import BlockchainService
from halocoin.service import Service


class ComplexEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (bytes, bytearray)):
            return obj.hex()
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


def blockchain_synced(func):
    def wrapper(*args, **kwargs):
        if engine.instance.blockchain.get_chain_state() == BlockchainService.IDLE:
            return func(*args, **kwargs)
        else:
            return 'Blockchain is syncing. This method is not reliable while operation continues.\n' + \
                   str(engine.instance.db.get('length')) + '-' + str(engine.instance.db.get('known_length'))

    # To keep the function name same for RPC helper
    wrapper.__name__ = func.__name__

    return wrapper


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.PackageLoader(__name__)
])
socketio = SocketIO(app)
listen_thread = None


def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


def run():
    def thread_target():
        socketio.run(app, host=host, port=engine.instance.config['port']['api'])

    global listen_thread
    host = os.environ.get('HALOCOIN_API_HOST', "0.0.0.0")
    listen_thread = threading.Thread(target=thread_target, daemon=True)
    listen_thread.start()
    print("Started API on {}:{}".format(host, engine.instance.config['port']['api']))
    import webbrowser
    webbrowser.open('http://' + host + ':' + str(engine.instance.config['port']['api']))


@socketio.on('connect')
def connect():
    print("%s connected" % request.sid)
    return ""


@app.route('/')
def dashboard():
    return render_template('index.html')


@app.route("/upload_wallet", methods=['GET', 'POST'])
def upload_wallet():
    wallet_name = request.values.get('wallet_name', None)
    wallet_file = request.files['wallet_file']
    wallet_content = wallet_file.stream.read()
    success = engine.instance.account.upload_wallet(wallet_name, wallet_content)
    return generate_json_response({
        "success": success,
        "wallet_name": wallet_name
    })


@app.route('/download_wallet', methods=['GET', 'POST'])
def download_wallet():
    wallet_name = request.values.get('wallet_name', None)
    if wallet_name is None:
        return generate_json_response({
            "success": False,
            "error": "Give a valid wallet name"
        })
    wallet_content = engine.instance.account.get_wallet(wallet_name)
    if wallet_content is None:
        return generate_json_response({
            "success": False,
            "error": "Wallet doesn't exist"
        })
    f = tempfile.NamedTemporaryFile()
    f.write(wallet_content)
    f.seek(0)
    return send_file(f, as_attachment=True, attachment_filename=wallet_name)


@app.route('/info_wallet', methods=['GET', 'POST'])
def info_wallet():
    from halocoin.model.wallet import Wallet
    wallet_name = request.values.get('wallet_name', None)
    password = request.values.get('password', None)
    if wallet_name is None:
        default_wallet = engine.instance.account.get_default_wallet()
        if default_wallet is not None:
            wallet_name = default_wallet['wallet_name']
            password = default_wallet['password']

    encrypted_wallet_content = engine.instance.account.get_wallet(wallet_name)
    if encrypted_wallet_content is not None:
        try:
            wallet = Wallet.from_string(tools.decrypt(password, encrypted_wallet_content))
            account = engine.instance.account.get_account(wallet.address, apply_tx_pool=True)
            return generate_json_response({
                "name": wallet.name,
                "pubkey": wallet.get_pubkey_str(),
                "privkey": wallet.get_privkey_str(),
                "address": wallet.address,
                "balance": account['amount']
            })
        except:
            return generate_json_response("Password incorrect")
    else:
        return generate_json_response("Error occurred")


@app.route('/remove_wallet', methods=['GET', 'POST'])
def remove_wallet():
    from halocoin.model.wallet import Wallet
    wallet_name = request.values.get('wallet_name', None)
    password = request.values.get('password', None)
    default_wallet = engine.instance.account.get_default_wallet()
    if default_wallet is not None and default_wallet['wallet_name'] == wallet_name:
        return generate_json_response({
            'success': False,
            'error': 'Cannot remove default wallet. First remove its default state!'
        })

    encrypted_wallet_content = engine.instance.account.get_wallet(wallet_name)
    if encrypted_wallet_content is not None:
        try:
            Wallet.from_string(tools.decrypt(password, encrypted_wallet_content))
            engine.instance.account.remove_wallet(wallet_name)
            return generate_json_response({
                "success": True,
                "message": "Successfully removed wallet"
            })
        except:
            return generate_json_response({
                "success": False,
                "error": "Password incorrect"
            })
    else:
        return generate_json_response({
                "success": False,
                "error": "Unidentified error occurred!"
            })


@app.route('/new_wallet', methods=['GET', 'POST'])
def new_wallet():
    from halocoin.model.wallet import Wallet
    wallet_name = request.values.get('wallet_name', None)
    pw = request.values.get('password', None)
    wallet = Wallet(wallet_name)
    success = engine.instance.account.new_wallet(pw, wallet)
    return generate_json_response({
        "name": wallet_name,
        "success": success
    })


@app.route('/wallets', methods=['GET', 'POST'])
def wallets():
    default_wallet = engine.instance.account.get_default_wallet()
    if default_wallet is None:
        wallet_name = ''
    else:
        wallet_name = default_wallet['wallet_name']
    return generate_json_response({
        'wallets': engine.instance.account.get_wallets(),
        'default_wallet': wallet_name
    })


@app.route('/peers', methods=['GET', 'POST'])
def peers():
    return generate_json_response(engine.instance.account.get_peers())


@app.route('/node_id', methods=['GET', 'POST'])
def node_id():
    return generate_json_response(engine.instance.db.get('node_id'))


@app.route('/set_default_wallet', methods=['GET', 'POST'])
def set_default_wallet():
    wallet_name = request.values.get('wallet_name', None)
    password = request.values.get('password', None)
    delete = request.values.get('delete', None)
    if delete is not None:
        return generate_json_response({
            "success": engine.instance.account.delete_default_wallet()
        })
    else:
        return generate_json_response({
            "success": engine.instance.account.set_default_wallet(wallet_name, password)
        })


@app.route('/history', methods=['GET', 'POST'])
#@blockchain_synced
def history():
    address = request.values.get('address', None)
    if address is None:
        address = engine.instance.db.get('address')
    account = engine.instance.account.get_account(address)
    txs = {
        "send": [],
        "recv": [],
        "mine": []
    }
    for block_index in reversed(account['tx_blocks']):
        block = engine.instance.db.get(str(block_index))
        for tx in block['txs']:
            tx['block'] = block_index
            owner = tools.tx_owner_address(tx)
            if owner == address:
                txs['send'].append(tx)
            elif tx['type'] == 'spend' and tx['to'] == address:
                txs['recv'].append(tx)
    for block_index in reversed(account['mined_blocks']):
        block = engine.instance.db.get(str(block_index))
        for tx in block['txs']:
            tx['block'] = block_index
            owner = tools.tx_owner_address(tx)
            if owner == address:
                txs['mine'].append(tx)
    return generate_json_response(txs)


@app.route('/send', methods=['GET', 'POST'])
#@blockchain_synced
def send():
    from halocoin.model.wallet import Wallet
    amount = int(request.values.get('amount', 0))
    address = request.values.get('address', None)
    message = request.values.get('message', '')
    wallet_name = request.values.get('wallet_name', None)
    password = request.values.get('password', None)

    if wallet_name is None:
        default_wallet = engine.instance.account.get_default_wallet()
        if default_wallet is not None:
            wallet_name = default_wallet['wallet_name']

    response = {"success": False}
    if amount <= 0:
        response['error'] = "Amount cannot be lower than or equalto 0"
        return generate_json_response(response)
    elif address is None:
        response['error'] = "You need to specify a receiving address for transaction"
        return generate_json_response(response)
    elif wallet_name is None:
        response['error'] = "Wallet name is not given and there is no default wallet"
        return generate_json_response(response)
    elif password is None:
        response['error'] = "Password missing!"
        return generate_json_response(response)

    tx = {'type': 'spend', 'amount': int(amount),
          'to': address, 'message': message}

    encrypted_wallet_content = engine.instance.account.get_wallet(wallet_name)
    if encrypted_wallet_content is not None:
        try:
            wallet = Wallet.from_string(tools.decrypt(password, encrypted_wallet_content))
        except:
            response['error'] = "Wallet password incorrect"
            return generate_json_response(response)
    else:
        response['error'] = "Error occurred"
        return generate_json_response(response)

    if 'count' not in tx:
        try:
            tx['count'] = engine.instance.account.known_tx_count(wallet.address)
        except:
            tx['count'] = 0
    if 'pubkeys' not in tx:
        tx['pubkeys'] = [wallet.get_pubkey_str()]  # We use pubkey as string
    if 'signatures' not in tx:
        tx['signatures'] = [tools.sign(tools.det_hash(tx), wallet.privkey)]
    engine.instance.blockchain.tx_queue.put(tx)
    response["success"] = True
    response["message"] = 'Tx amount:{} to:{} added to the pool'.format(tx['amount'], tx['to'])
    return generate_json_response(response)


@app.route('/blockcount', methods=['GET', 'POST'])
def blockcount():
    result = dict(length=engine.instance.db.get('length'),
                  known_length=engine.instance.db.get('known_length'))
    result_text = json.dumps(result)
    return Response(response=result_text, headers={"Content-Type": "application/json"})


@app.route('/txs', methods=['GET', 'POST'])
def txs():
    pool = engine.instance.blockchain.tx_pool()
    for i, tx in enumerate(pool):
        pool[i]['from'] = tools.tx_owner_address(tx)
    return generate_json_response(pool)


@app.route('/block', methods=['GET', 'POST'])
def block():
    start = int(request.values.get('start', '-1'))
    end = int(request.values.get('end', '-1'))
    length = engine.instance.db.get('length')
    if start == -1 and end == -1:
        end = length
        start = max(end-20, 0)
    elif start == -1:
        start = max(end - 20, 0)
    elif end == -1:
        end = min(length, start+20)

    result = {
        "start": start,
        "end": end,
        "blocks": []
    }
    for i in range(start, end+1):
        block = engine.instance.db.get(str(i))
        if block is None:
            break
        mint_tx = list(filter(lambda t: t['type'] == 'mint', block['txs']))[0]
        block['miner'] = tools.tx_owner_address(mint_tx)
        result["blocks"].append(block)
    result["blocks"] = list(reversed(result["blocks"]))
    return generate_json_response(result)


@app.route('/difficulty', methods=['GET', 'POST'])
#@blockchain_synced
def difficulty():
    diff = engine.instance.blockchain.target(engine.instance.db.get('length'))
    return generate_json_response({"difficulty": diff})


@app.route('/balance', methods=['GET', 'POST'])
#@blockchain_synced
def balance():
    from halocoin.model.wallet import Wallet
    address = request.values.get('address', None)
    if address is None:
        default_wallet = engine.instance.account.get_default_wallet()
        if default_wallet is not None:
            wallet_name = default_wallet['wallet_name']
            password = default_wallet['password']
            encrypted_wallet_content = engine.instance.account.get_wallet(wallet_name)
            wallet = Wallet.from_string(tools.decrypt(password, encrypted_wallet_content))
            address = wallet.address

    account = engine.instance.account.get_account(address, apply_tx_pool=True)
    return generate_json_response({'balance': account['amount']})


@app.route('/stop', methods=['GET', 'POST'])
def stop():
    engine.instance.db.put('stop', True)
    shutdown_server()
    print('Closed API')
    engine.instance.stop()
    return generate_json_response('Shutting down')


@app.route('/start_miner', methods=['GET', 'POST'])
def start_miner():
    from halocoin.model.wallet import Wallet
    wallet_name = request.values.get('wallet_name', None)
    password = request.values.get('password', None)

    if wallet_name is None:
        default_wallet = engine.instance.account.get_default_wallet()
        if default_wallet is not None:
            wallet_name = default_wallet['wallet_name']
            password = default_wallet['password']

    encrypted_wallet_content = engine.instance.account.get_wallet(wallet_name)
    if encrypted_wallet_content is not None:
        try:
            wallet = Wallet.from_string(tools.decrypt(password, encrypted_wallet_content))
        except:
            return generate_json_response("Wallet password incorrect")
    else:
        return generate_json_response("Error occurred")

    if engine.instance.miner.get_state() == Service.RUNNING:
        return generate_json_response('Miner is already running.')
    elif wallet is None:
        return generate_json_response('Given wallet is not valid.')
    else:
        engine.instance.miner.set_wallet(wallet)
        engine.instance.miner.register()
        return generate_json_response('Running miner')


@app.route('/stop_miner', methods=['GET', 'POST'])
def stop_miner():
    if engine.instance.miner.get_state() == Service.RUNNING:
        engine.instance.miner.unregister()
        return 'Closed miner'
    else:
        return 'Miner is not running.'


@app.route('/status_miner', methods=['GET', 'POST'])
def status_miner():
    return generate_json_response({
        'running': engine.instance.miner.get_state() == Service.RUNNING
    })


def generate_json_response(obj):
    result_text = json.dumps(obj, cls=ComplexEncoder)
    return Response(response=result_text, headers={"Content-Type": "application/json"})


@app.route('/notify')
def notify():
    new_block()
    return "ok"


def new_block():
    socketio.emit('new_block')


def peer_update():
    socketio.emit('peer_update')


def new_tx_in_pool():
    socketio.emit('new_tx_in_pool')