SELECT
	au.NAME as name,
	auc.cabang_id,
	karyawan.nik,
	au.id
FROM
	authenticated_users au
	JOIN authenticated_user_roles aur ON aur.authenticated_user_id = au.id
	JOIN roles r ON r.id = aur.role_id
	LEFT JOIN karyawan on karyawan.authenticated_user_id = au.id
	LEFT JOIN authenticated_user_cabang auc ON auc.authenticated_user_id = au.id
	LEFT JOIN cabang c ON c.id = auc.cabang_id 
WHERE
	r.CODE = 'driver' and auc.cabang_id = 1