package kr.co.sample.service.impl;

import kr.co.sample.service.UserService;

public class UserServiceImpl implements UserService {

    private UserDAO userDAO;

    public Object selectUserList() {
        return userDAO.selectUserList();
    }

    public void insertUser(Object vo) {
        userDAO.insertUser(vo);
    }
}
