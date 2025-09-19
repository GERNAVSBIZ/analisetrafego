# -*- coding: utf-8 -*-

from flask import Flask, render_template, request, jsonify
import pandas as pd
import re
from datetime import datetime
import io
import os
import json
import firebase_admin
from firebase_admin import credentials, auth, firestore

app = Flask(__name__)
db = None
try:
    # ... (bloco de inicialização do Firebase sem alterações) ...
    creds_json_str = os.environ.get('FIREBASE_CREDENTIALS_JSON')
    if creds_json_str:
        creds_dict = json.loads(creds_json_str)
        cred = credentials.Certificate(creds_dict)
    else:
        cred = credentials.Certificate('firebase-credentials.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase Admin SDK e Firestore inicializados com sucesso.")
except Exception as e:
    print(f"ERRO: Falha ao inicializar o Firebase Admin SDK: {e}")

# ... (função parse_data_file sem alterações) ...
def parse_data_file(file_content):
    lines = file_content.split('\n')
    records = []
    icao_code = 'SBIZ'
    data_date = None
    commercial_prefixes = ['AZU', 'GLO', 'TAM']
    for line in lines:
        if len(line.strip()) < 30 or line.startswith('SBIZAIZ0'):
            continue
        record = {
            'timestamp': None, 'matricula': 'N/A', 'tipo_aeronave': 'N/A',
            'origem': 'N/A', 'destino': 'N/A', 'regra_voo': 'N/A',
            'pista': '', 'responsavel': 'N/A', 'flight_class': 'N/A'
        }
        try:
            data_str_header = line[9:15]
            data_block = line[15:].strip()
            is_commercial = any(data_block.startswith(prefix) for prefix in commercial_prefixes)
            if is_commercial:
                record['matricula'] = data_block[:7]
                acft_block = data_block[7:]
                acft_match = re.search(r'([A-Z0-9]+)([GSNM])', acft_block)
                if acft_match:
                    record['tipo_aeronave'] = acft_match.group(1)
                    record['flight_class'] = acft_match.group(2)
                    route_block = acft_block[acft_match.end():].strip()
                else:
                    route_block = acft_block
            else:
                parts = data_block.split(None, 1)
                record['matricula'] = parts[0]
                if len(parts) > 1:
                    acft_block = parts[1]
                    acft_match = re.search(r'([A-Z0-9]+)([GSNM])', acft_block)
                    if acft_match:
                        record['tipo_aeronave'] = acft_match.group(1)
                        record['flight_class'] = acft_match.group(2)
                        route_block = acft_block[acft_match.end():].strip()
                    else:
                        route_block = acft_block
                else:
                    route_block = ""
            rule_match = re.search(r'(IV|VV)', route_block)
            if rule_match:
                record['regra_voo'] = rule_match.group(1).replace('IV', 'IFR').replace('VV', 'VFR')
                after_rule_block = route_block[rule_match.end():].strip()
                route_block = route_block[:rule_match.start()].strip()
                pista_match = re.search(r'(07|25)', after_rule_block)
                if pista_match:
                    record['pista'] = pista_match.group(1)
                if after_rule_block:
                    record['responsavel'] = after_rule_block.split()[-1]
            time_match = re.search(r'(\d{4})', route_block)
            horario_str = ''
            if time_match:
                horario_str = time_match.group(1)
                route_block = route_block.replace(horario_str, '', 1)
            icao_codes = re.findall(r'S[A-Z0-9]{3}', route_block)
            if len(icao_codes) >= 2:
                record['destino'] = icao_codes[0]
                record['origem'] = icao_codes[1]
            elif len(icao_codes) == 1:
                if record['pista']:
                    record['origem'] = icao_code
                    record['destino'] = icao_codes[0]
                else:
                    record['origem'] = icao_codes[0]
                    record['destino'] = icao_code
            else:
                record['origem'] = icao_code
                record['destino'] = icao_code
            if horario_str:
                try:
                    dt_obj = datetime.strptime(f"{data_str_header}{horario_str}", '%d%m%y%H%M')
                    record['timestamp'] = dt_obj.isoformat() + 'Z'
                    if data_date is None: data_date = record['timestamp']
                except (ValueError, IndexError): pass
            records.append(record)
        except Exception as e:
            print(f"Erro ao processar linha: '{line.strip()}'. Erro: {e}")
    return {"records": records, "icao_code": "SBIZ", "data_date": data_date}

@app.route('/')
def index():
    return render_template('index.html')

# vvvvvv ROTA DE UPLOAD ATUALIZADA PARA MÚLTIPLOS ARQUIVOS vvvvvv
@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        print(f"Upload autorizado para o usuário: {decoded_token['uid']}")
    except Exception as e:
        return jsonify({"error": "Token inválido ou expirado"}), 401
    
    # Usa getlist para receber múltiplos arquivos com a mesma chave 'dataFiles'
    files = request.files.getlist('dataFiles')

    if not files or all(f.filename == '' for f in files):
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    all_records = []
    first_data_date = None

    for file in files:
        if file.filename != '':
            try:
                content = io.StringIO(file.stream.read().decode("utf-8", errors='ignore')).getvalue()
                parsed_data = parse_data_file(content)
                all_records.extend(parsed_data["records"])
                
                # Guarda a data do primeiro arquivo para usar como referência
                if first_data_date is None:
                    first_data_date = parsed_data["data_date"]
            except Exception as e:
                # Se um arquivo falhar, podemos pular ou retornar um erro
                print(f"Erro ao processar o arquivo {file.filename}: {e}")
                continue # Pula para o próximo arquivo

    if not all_records:
        return jsonify({"error": "Nenhum registro válido encontrado nos arquivos"}), 400

    return jsonify({
        "records": all_records,
        "icao_code": "SBIZ", # Mantém o ICAO fixo por enquanto
        "data_date": first_data_date # Usa a data do primeiro arquivo como referência
    })
# ^^^^^^ FIM DA ROTA ATUALIZADA ^^^^^^


# ... (rotas /api/save_records, /api/get_uploads, etc. sem alterações) ...
@app.route('/api/save_records', methods=['POST'])
def save_records():
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Token inválido ou expirado"}), 401
    if not db:
        return jsonify({"error": "Conexão com o banco de dados não está disponível"}), 500
    try:
        data_to_save = request.get_json()
        records_to_save = data_to_save.get('records')
        icao_code = data_to_save.get('icao_code')
        data_date = data_to_save.get('data_date')
        if not records_to_save or not isinstance(records_to_save, list):
            return jsonify({"error": "Dados inválidos ou vazios"}), 400
        upload_ref = db.collection('flight_uploads').document()
        upload_ref.set({
            'userId': user_id, 'createdAt': firestore.SERVER_TIMESTAMP,
            'recordCount': len(records_to_save), 'icaoCode': icao_code, 'dataDate': data_date
        })
        batch = db.batch()
        for i, rec in enumerate(records_to_save):
            doc_ref = upload_ref.collection('records').document()
            batch.set(doc_ref, rec)
            if (i + 1) % 500 == 0:
                batch.commit()
                batch = db.batch()
        batch.commit()
        return jsonify({"success": True, "message": f"{len(records_to_save)} registros salvos com sucesso!", "documentId": upload_ref.id}), 201
    except Exception as e:
        print(f"ERRO ao salvar no Firestore: {e}")
        return jsonify({"error": f"Erro interno ao salvar os dados: {str(e)}"}), 500
@app.route('/api/get_uploads', methods=['GET'])
def get_uploads():
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    try:
        uploads_ref = db.collection('flight_uploads')
        query = uploads_ref.where('userId', '==', user_id).order_by('createdAt', direction=firestore.Query.DESCENDING)
        results = []
        for doc in query.stream():
            doc_data = doc.to_dict()
            results.append({
                'uploadId': doc.id, 'createdAt': doc_data['createdAt'].isoformat(),
                'recordCount': doc_data.get('recordCount'), 'icaoCode': doc_data.get('icaoCode', None),
                'dataDate': doc_data.get('dataDate', None)
            })
        return jsonify(results), 200
    except Exception as e:
        print(f"ERRO ao buscar uploads: {e}")
        return jsonify({"error": "Não foi possível buscar o histórico de uploads."}), 500
@app.route('/api/get_records/<upload_id>', methods=['GET'])
def get_records(upload_id):
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    try:
        upload_doc = db.collection('flight_uploads').document(upload_id).get()
        if not upload_doc.exists or upload_doc.to_dict()['userId'] != user_id:
            return jsonify({"error": "Acesso não autorizado ou upload não encontrado"}), 403
        records_ref = db.collection('flight_uploads').document(upload_id).collection('records')
        records = [doc.to_dict() for doc in records_ref.stream()]
        return jsonify(records), 200
    except Exception as e:
        print(f"ERRO ao buscar registros do upload {upload_id}: {e}")
        return jsonify({"error": "Não foi possível buscar os registros."}), 500
@app.route('/api/delete_upload/<upload_id>', methods=['DELETE'])
def delete_upload(upload_id):
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    try:
        upload_ref = db.collection('flight_uploads').document(upload_id)
        upload_doc = upload_ref.get()
        if not upload_doc.exists:
            return jsonify({"error": "Upload não encontrado"}), 404
        if upload_doc.to_dict()['userId'] != user_id:
            return jsonify({"error": "Acesso não autorizado"}), 403
        records_ref = upload_ref.collection('records')
        docs = records_ref.limit(500).stream()
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        upload_ref.delete()
        return jsonify({"success": True, "message": "Registro apagado com sucesso!"}), 200
    except Exception as e:
        print(f"ERRO ao apagar o upload {upload_id}: {e}")
        return jsonify({"error": "Não foi possível apagar o registro."}), 500
@app.route('/api/get_aggregated_data', methods=['GET'])
def get_aggregated_data():
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    if not start_date_str or not end_date_str:
        return jsonify({"error": "As datas de início e fim são obrigatórias"}), 400
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        uploads_ref = db.collection('flight_uploads')
        query = uploads_ref.where('userId', '==', user_id).where('dataDate', '>=', start_date.isoformat() + 'Z').where('dataDate', '<=', end_date.isoformat() + 'Z')
        relevant_uploads = [doc.id for doc in query.stream()]
        all_records = []
        for upload_id in relevant_uploads:
            records_ref = db.collection('flight_uploads').document(upload_id).collection('records')
            records = [doc.to_dict() for doc in records_ref.stream()]
            all_records.extend(records)
        return jsonify(all_records), 200
    except Exception as e:
        print(f"ERRO ao agregar dados: {e}")
        return jsonify({"error": "Não foi possível processar a solicitação."}), 500

if __name__ == '__main__':
    app.run(debug=True)
