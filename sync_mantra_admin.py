@app.route('/sync-mantra-admin', methods=['POST'])
def sync_mantra_admin():

    try:

        # -------------------------------------------------
        # OPTIONS FROM UI
        # -------------------------------------------------

        active_only = (
            request.form.get('active_only', 'false')
            .lower()
            == 'true'
        )

        source = request.form.get(
            'source',
            'both'
        )


        # -------------------------------------------------
        # FETCH FROM MANTRA
        # -------------------------------------------------

        employees = fetch_mantra_employees(
            active_only=active_only,
            source=source
        )


        # -------------------------------------------------
        # SYNCHRONIZE TO POSTGRESQL
        # -------------------------------------------------

        synced_count = 0

        for employee in employees:

            # ---------------------------------------------
            # YOUR EXISTING INSERT / UPDATE LOGIC
            # ---------------------------------------------

            sync_employee_to_postgresql(
                employee
            )

            synced_count += 1


        # -------------------------------------------------
        # RETURN COMPLETE DATA TO UI
        # -------------------------------------------------

        return jsonify({

            'success': True,

            'synced_count': synced_count,

            'employees': employees

        })


    except Exception as e:

        import traceback

        traceback.print_exc()


        return jsonify({

            'success': False,

            'error': str(e),

            'employees': []

        }), 500